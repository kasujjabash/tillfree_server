"""Plans (catalogue), the organisation's subscription, and platform billing operations."""

from typing import Any, ClassVar
from uuid import UUID

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.public import IsPlatformStaff, TenantAPIView, seats_in_use
from apps.documents.public import storage_used_bytes
from apps.identity.public import User
from apps.organisations.public import active_branch_count
from common.api.pagination import SmallListPagination

from .. import services
from ..entitlements import LIMITS
from ..models import Interval, Plan, Subscription, SubscriptionInvoice


# ------------------------------------------------------------------ serializers
class PriceSerializer(serializers.Serializer[Any]):
    currency = serializers.CharField()
    amount = serializers.DecimalField(max_digits=19, decimal_places=4, coerce_to_string=True)
    interval = serializers.CharField()


class PlanSerializer(serializers.ModelSerializer[Plan]):
    modules = serializers.SerializerMethodField()
    prices = PriceSerializer(many=True, read_only=True)

    class Meta:
        model = Plan
        fields = ("code", "name", "description", "trial_days", "limits", "modules", "prices")

    def get_modules(self, obj: Plan) -> list[str]:
        return sorted(e.module_id for e in obj.entitlements.all())


class LimitUsageSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    description = serializers.CharField()
    limit = serializers.IntegerField(allow_null=True)
    used = serializers.IntegerField()


class SubscriptionSerializer(serializers.Serializer[Any]):
    plan_code = serializers.CharField(allow_null=True)
    plan_name = serializers.CharField(allow_null=True)
    status = serializers.CharField(allow_null=True)
    interval = serializers.CharField(allow_null=True)
    current_period_start = serializers.DateTimeField(allow_null=True)
    current_period_end = serializers.DateTimeField(allow_null=True)
    trial_ends_at = serializers.DateTimeField(allow_null=True)
    cancel_at_period_end = serializers.BooleanField()
    modules = serializers.ListField(child=serializers.CharField())
    usage = LimitUsageSerializer(many=True)


class ChangePlanSerializer(serializers.Serializer[Any]):
    plan_code = serializers.SlugField()
    interval = serializers.ChoiceField(choices=Interval.choices, default=Interval.MONTH)


class InvoiceSerializer(serializers.ModelSerializer[SubscriptionInvoice]):
    amount = serializers.DecimalField(max_digits=19, decimal_places=4, coerce_to_string=True)

    class Meta:
        model = SubscriptionInvoice
        fields = (
            "id",
            "number",
            "period_start",
            "period_end",
            "amount",
            "currency",
            "status",
            "issued_at",
            "due_at",
            "paid_at",
        )


class OverrideSerializer(serializers.Serializer[Any]):
    module = serializers.CharField(required=False, allow_null=True, default=None)
    enabled = serializers.BooleanField(required=False, allow_null=True, default=None)
    limits = serializers.DictField(
        child=serializers.IntegerField(allow_null=True, min_value=0), required=False, default=dict
    )
    reason = serializers.CharField(max_length=300)
    expires_at = serializers.DateTimeField(required=False, allow_null=True, default=None)


def _usage() -> dict[str, int]:
    return {
        "max_members": seats_in_use(),
        "max_branches": active_branch_count(),
        "storage_bytes": storage_used_bytes(),
    }


# ------------------------------------------------------------------ views
class PlanListView(APIView):
    @extend_schema(responses={200: PlanSerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(PlanSerializer(services.public_plans(), many=True).data)


class SubscriptionView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"get": "subscriptions.subscription.view"}

    @extend_schema(responses={200: SubscriptionSerializer})
    def get(self, request: Request) -> Response:
        subscription: Subscription | None = services.current_subscription()
        entitlements = self.ctx.entitlements
        usage = _usage()
        body = {
            "plan_code": entitlements.plan_code,
            "plan_name": subscription.plan.name if subscription else None,
            "status": entitlements.status,
            "interval": subscription.interval if subscription else None,
            "current_period_start": subscription.current_period_start if subscription else None,
            "current_period_end": subscription.current_period_end if subscription else None,
            "trial_ends_at": subscription.trial_ends_at if subscription else None,
            "cancel_at_period_end": bool(subscription and subscription.cancel_at_period_end),
            "modules": sorted(entitlements.modules),
            "usage": [
                {
                    "key": key,
                    "description": text,
                    "limit": entitlements.limit(key),
                    "used": usage[key],
                }
                for key, text in LIMITS.items()
            ],
        }
        return Response(SubscriptionSerializer(body).data)


class ChangePlanView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"post": "subscriptions.subscription.manage"}

    @extend_schema(request=ChangePlanSerializer, responses={200: SubscriptionSerializer})
    def post(self, request: Request) -> Response:
        data = ChangePlanSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        services.change_plan(
            organisation_id=self.ctx.organisation_id,
            plan_code=data.validated_data["plan_code"],
            interval=data.validated_data["interval"],
            actor_id=self.ctx.user_id,
        )
        return Response({"detail": "Plan changed."})


class CancelView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {
        "post": "subscriptions.subscription.manage",
        "delete": "subscriptions.subscription.manage",
    }

    @extend_schema(request=None, responses={200: None})
    def post(self, request: Request) -> Response:
        """Cancel at the end of the current period."""
        services.set_cancel_at_period_end(
            organisation_id=self.ctx.organisation_id, cancel=True, actor_id=self.ctx.user_id
        )
        return Response({"detail": "The subscription will end at the end of the current period."})

    @extend_schema(request=None, responses={200: None})
    def delete(self, request: Request) -> Response:
        """Withdraw a scheduled cancellation."""
        services.set_cancel_at_period_end(
            organisation_id=self.ctx.organisation_id, cancel=False, actor_id=self.ctx.user_id
        )
        return Response({"detail": "The subscription will continue."})


class InvoiceListView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"get": "subscriptions.invoice.view"}

    @extend_schema(responses={200: InvoiceSerializer(many=True)})
    def get(self, request: Request) -> Response:
        paginator = SmallListPagination()
        page = paginator.paginate_queryset(services.list_invoices(), request, view=self)
        return paginator.get_paginated_response(InvoiceSerializer(page, many=True).data)


class PlatformOverrideView(APIView):
    permission_classes = (IsPlatformStaff,)

    @extend_schema(request=OverrideSerializer, responses={201: None})
    def post(self, request: Request, organisation_id: UUID) -> Response:
        data = OverrideSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        if not isinstance(request.user, User):
            raise NotAuthenticated()
        override = services.grant_override(
            organisation_id=organisation_id,
            module_code=v["module"],
            enabled=v["enabled"],
            limits=v["limits"],
            reason=v["reason"],
            granted_by=request.user,
            expires_at=v["expires_at"],
        )
        return Response({"id": str(override.id)}, status=status.HTTP_201_CREATED)


class PlatformInvoicePaidView(APIView):
    permission_classes = (IsPlatformStaff,)

    @extend_schema(request=None, responses={200: InvoiceSerializer})
    def post(self, request: Request, organisation_id: UUID, invoice_id: UUID) -> Response:
        invoice = services.mark_invoice_paid(
            organisation_id=organisation_id, invoice_id=invoice_id, actor=request.user
        )
        return Response(InvoiceSerializer(invoice).data)
