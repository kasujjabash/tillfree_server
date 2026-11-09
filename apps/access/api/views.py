"""Access endpoints: organisation creation, my access, members, roles, invitations,
support access and platform administration."""

from typing import Any, ClassVar

import django_filters  # type: ignore[import-untyped]  # no stubs published
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from apps.identity.public import ClientInfo, User, token_response
from apps.organisations.public import list_organisations
from common.api.mixins import UserScopedMixin
from common.api.pagination import SmallListPagination
from common.api.request import client_ip, user_agent
from common.exceptions import NotFoundError

from .. import registry, selectors
from ..authorization import all_permissions
from ..models import Invitation, Membership, Role, RoleAssignment, SupportAccessGrant
from ..services import invitations, members, onboarding, support
from . import serializers as s
from .base import IsPlatformStaff, TenantAPIMixin, TenantAPIView

UUID_LOOKUP = r"[0-9a-f-]{36}"


# Filters are declared explicitly: django-filter's automatic filters would query the
# tenant-scoped default manager while building the class (e.g. for the API schema).
class MembershipFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Membership.Status.choices)


class InvitationFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=Invitation.Status.choices)


class SupportGrantFilter(django_filters.FilterSet):
    status = django_filters.ChoiceFilter(choices=SupportAccessGrant.Status.choices)


class AssignmentFilter(django_filters.FilterSet):
    # Plain UUID filters: the result set is already limited to the organisation.
    membership = django_filters.UUIDFilter(field_name="membership_id")
    branch = django_filters.UUIDFilter(field_name="branch_id")


def _user(request: Request) -> User:
    if not isinstance(request.user, User):
        raise NotAuthenticated()
    return request.user


def _found(obj: Any) -> Any:
    if obj is None:
        raise NotFoundError(_("Not found."))
    return obj


# ------------------------------------------------------------------ not tenant-scoped
class OrganisationCreateView(APIView):
    throttle_classes = (
        ScopedRateThrottle,
        UserRateThrottle,
    )
    throttle_scope = "organisation_create"

    @extend_schema(
        request=s.OrganisationCreateSerializer, responses={201: s.OrganisationSummarySerializer}
    )
    def post(self, request: Request) -> Response:
        data = s.OrganisationCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        organisation = onboarding.create_organisation(
            _user(request), dict(data.validated_data), ip_address=client_ip(request)
        )
        return Response(
            s.OrganisationSummarySerializer(organisation).data, status=status.HTTP_201_CREATED
        )


class MyMembershipsView(UserScopedMixin, APIView):
    @extend_schema(responses={200: s.MyMembershipSerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(
            s.MyMembershipSerializer(selectors.my_memberships(_user(request)), many=True).data
        )


class PermissionListView(APIView):
    @extend_schema(responses={200: s.PermissionSerializer(many=True)})
    def get(self, request: Request) -> Response:
        items = [
            {
                "code": p.code,
                "module": p.module,
                "description": p.description,
                "sensitivity": p.sensitivity,
            }
            for p in sorted(registry.permissions().values(), key=lambda p: p.code)
        ]
        return Response(s.PermissionSerializer(items, many=True).data)


class RoleTemplateListView(APIView):
    @extend_schema(responses={200: s.RoleTemplateSerializer(many=True)})
    def get(self, request: Request) -> Response:
        items = [
            {
                "key": key,
                "name": registry.template_name(key),
                "description": registry.template_description(key),
                "permissions": sorted(registry.template_permissions(key)),
            }
            for key in registry.template_keys()
        ]
        return Response(s.RoleTemplateSerializer(items, many=True).data)


# ------------------------------------------------------------------ my access (tenant)
class MyAccessView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"get": None}

    @extend_schema(responses={200: s.MyAccessSerializer})
    def get(self, request: Request) -> Response:
        ctx = self.ctx
        body = {
            "organisation": ctx.organisation,
            "membership_id": ctx.membership.id if ctx.membership else None,
            "permissions": sorted(all_permissions(ctx)),
            "grants": [
                {"branch_id": g.branch_id, "permissions": sorted(g.permissions)} for g in ctx.grants
            ],
            "mfa_satisfied": ctx.mfa_satisfied,
            "require_mfa": ctx.require_mfa,
            "support_access": ctx.is_support,
        }
        return Response(s.MyAccessSerializer(body).data)


# ------------------------------------------------------------------ members
class MembershipViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Membership],
):
    queryset = Membership.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.MembershipSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = MembershipFilter
    search_fields: ClassVar[list[str]] = ["user__email", "user__full_name"]
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "access.membership.view",
        "retrieve": "access.membership.view",
        "partial_update": "access.membership.manage",
        "destroy": "access.membership.manage",
        "suspend": "access.membership.manage",
        "reactivate": "access.membership.manage",
        "leave": None,
    }

    def get_queryset(self) -> QuerySet[Membership]:
        return selectors.list_memberships().order_by("user__email")

    @extend_schema(request=s.MembershipSerializer, responses={200: s.MembershipSerializer})
    def partial_update(self, request: Request, pk: str) -> Response:
        membership = self.get_object()
        data = s.MembershipSerializer(membership, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        members.update_membership(
            self.ctx,
            membership,
            department=data.validated_data.get("department", membership.department),
        )
        return Response(s.MembershipSerializer(selectors.get_membership(membership.id)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request: Request, pk: str) -> Response:
        members.remove_membership(self.ctx, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(request=None, responses={200: s.MembershipSerializer})
    @action(detail=True, methods=["post"])
    def suspend(self, request: Request, pk: str) -> Response:
        membership = members.suspend_membership(self.ctx, self.get_object())
        return Response(s.MembershipSerializer(selectors.get_membership(membership.id)).data)

    @extend_schema(request=None, responses={200: s.MembershipSerializer})
    @action(detail=True, methods=["post"])
    def reactivate(self, request: Request, pk: str) -> Response:
        membership = members.reactivate_membership(self.ctx, self.get_object())
        return Response(s.MembershipSerializer(selectors.get_membership(membership.id)).data)

    @extend_schema(request=None, responses={204: None})
    @action(detail=False, methods=["post"])
    def leave(self, request: Request) -> Response:
        members.leave_organisation(self.ctx)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ------------------------------------------------------------------ roles
class RoleViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Role],
):
    queryset = Role.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.RoleSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "access.role.view",
        "retrieve": "access.role.view",
        "create": "access.role.manage",
        "partial_update": "access.role.manage",
        "destroy": "access.role.manage",
    }

    def get_queryset(self) -> QuerySet[Role]:
        return selectors.list_roles()

    @extend_schema(request=s.RoleSerializer, responses={201: s.RoleSerializer})
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        data = s.RoleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        role = members.create_role(self.ctx, **data.validated_data)
        return Response(s.RoleSerializer(role).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.RoleSerializer, responses={200: s.RoleSerializer})
    def partial_update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        role = self.get_object()
        data = s.RoleSerializer(role, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        role = members.update_role(self.ctx, role, dict(data.validated_data))
        return Response(s.RoleSerializer(role).data)

    @extend_schema(responses={204: None})
    def destroy(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        members.delete_role(self.ctx, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


class RoleAssignmentViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet[RoleAssignment],
):
    queryset = RoleAssignment.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.AssignmentSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = AssignmentFilter
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "access.role.view",
        "create": "access.role.assign",
        "destroy": "access.role.assign",
    }

    def get_queryset(self) -> QuerySet[RoleAssignment]:
        return selectors.list_assignments().order_by("created_at")

    @extend_schema(request=s.AssignmentCreateSerializer, responses={201: s.AssignmentSerializer})
    def create(self, request: Request) -> Response:
        data = s.AssignmentCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        assignment = members.assign_role(
            self.ctx,
            v["membership"],
            template_key=v["template_key"],
            role=v["role"],
            branch=v["branch"],
        )
        return Response(s.AssignmentSerializer(assignment).data, status=status.HTTP_201_CREATED)

    def perform_destroy(self, instance: RoleAssignment) -> None:
        members.remove_assignment(self.ctx, instance)


# ------------------------------------------------------------------ invitations
class InvitationViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Invitation],
):
    queryset = Invitation.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.InvitationSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = InvitationFilter
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "access.invitation.manage",
        "retrieve": "access.invitation.manage",
        "create": "access.invitation.manage",
        "resend": "access.invitation.manage",
        "revoke": "access.invitation.manage",
    }

    def get_queryset(self) -> QuerySet[Invitation]:
        return selectors.list_invitations().order_by("-created_at")

    @extend_schema(request=s.InvitationCreateSerializer, responses={201: s.InvitationSerializer})
    def create(self, request: Request) -> Response:
        data = s.InvitationCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        invitation = invitations.create_invitation(
            self.ctx,
            email=v["email"],
            template_key=v["template_key"],
            role=v["role"],
            branch=v["branch"],
        )
        return Response(s.InvitationSerializer(invitation).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=None, responses={200: s.InvitationSerializer})
    @action(detail=True, methods=["post"])
    def resend(self, request: Request, pk: str) -> Response:
        return Response(
            s.InvitationSerializer(invitations.resend_invitation(self.ctx, self.get_object())).data
        )

    @extend_schema(request=None, responses={200: s.InvitationSerializer})
    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: str) -> Response:
        return Response(
            s.InvitationSerializer(invitations.revoke_invitation(self.ctx, self.get_object())).data
        )


class _InvitationPublicView(APIView):
    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        ScopedRateThrottle,
        AnonRateThrottle,
    )
    throttle_scope = "auth_token"


class InvitationPreviewView(_InvitationPublicView):
    @extend_schema(
        request=s.InvitationTokenSerializer, responses={200: s.InvitationPreviewSerializer}
    )
    def post(self, request: Request) -> Response:
        data = s.InvitationTokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        preview = invitations.preview_invitation(data.validated_data["token"])
        return Response(s.InvitationPreviewSerializer(preview.__dict__).data)


class InvitationAcceptView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (
        ScopedRateThrottle,
        UserRateThrottle,
    )
    throttle_scope = "auth_token"

    @extend_schema(
        request=s.InvitationTokenSerializer, responses={200: s.OrganisationSummarySerializer}
    )
    def post(self, request: Request) -> Response:
        data = s.InvitationTokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        membership = invitations.accept_invitation(_user(request), data.validated_data["token"])
        return Response(s.OrganisationSummarySerializer(membership.organisation).data)


class InvitationAcceptNewView(_InvitationPublicView):
    @extend_schema(request=s.InvitationAcceptNewSerializer, responses={201: None})
    def post(self, request: Request) -> Response:
        data = s.InvitationAcceptNewSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        tokens = invitations.accept_invitation_as_new_user(
            v["token"],
            full_name=v["full_name"],
            password=v["password"],
            client=ClientInfo(
                ip_address=client_ip(request),
                user_agent=user_agent(request),
                device_label=v["device_label"],
            ),
        )
        return token_response(tokens, use_cookie=v["use_cookie"], status=status.HTTP_201_CREATED)


# ------------------------------------------------------------- support access (organisation side)
class SupportGrantViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[SupportAccessGrant],
):
    queryset = SupportAccessGrant.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.SupportGrantSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = SupportGrantFilter
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "access.support.manage",
        "retrieve": "access.support.manage",
        "approve": "access.support.manage",
        "deny": "access.support.manage",
        "revoke": "access.support.manage",
    }

    def get_queryset(self) -> QuerySet[SupportAccessGrant]:
        return selectors.list_support_grants().order_by("-created_at")

    @extend_schema(request=None, responses={200: s.SupportGrantSerializer})
    @action(detail=True, methods=["post"])
    def approve(self, request: Request, pk: str) -> Response:
        return Response(
            s.SupportGrantSerializer(support.decide(self.ctx, self.get_object(), approve=True)).data
        )

    @extend_schema(request=None, responses={200: s.SupportGrantSerializer})
    @action(detail=True, methods=["post"])
    def deny(self, request: Request, pk: str) -> Response:
        return Response(
            s.SupportGrantSerializer(
                support.decide(self.ctx, self.get_object(), approve=False)
            ).data
        )

    @extend_schema(request=None, responses={200: s.SupportGrantSerializer})
    @action(detail=True, methods=["post"])
    def revoke(self, request: Request, pk: str) -> Response:
        return Response(s.SupportGrantSerializer(support.revoke(self.ctx, self.get_object())).data)


# ------------------------------------------------------------------ platform
class PlatformOrganisationListView(APIView):
    permission_classes = (IsPlatformStaff,)

    @extend_schema(responses={200: s.PlatformOrganisationSerializer(many=True)})
    def get(self, request: Request) -> Response:
        paginator = SmallListPagination()
        page = paginator.paginate_queryset(list_organisations(), request, view=self)
        return paginator.get_paginated_response(
            s.PlatformOrganisationSerializer(page, many=True).data
        )


class PlatformSupportGrantView(UserScopedMixin, APIView):
    permission_classes = (IsPlatformStaff,)

    @extend_schema(responses={200: s.SupportGrantSerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(
            s.SupportGrantSerializer(selectors.my_support_grants(_user(request)), many=True).data
        )

    @extend_schema(request=s.SupportRequestSerializer, responses={201: s.SupportGrantSerializer})
    def post(self, request: Request) -> Response:
        data = s.SupportRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        grant = support.request_access(_user(request), **data.validated_data)
        return Response(s.SupportGrantSerializer(grant).data, status=status.HTTP_201_CREATED)
