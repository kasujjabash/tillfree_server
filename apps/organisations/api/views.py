"""Organisation profile, settings, branches and departments (tenant-scoped)."""

from typing import Any, ClassVar

import django_filters  # type: ignore[import-untyped]  # no stubs published
from django.db.models import QuerySet
from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response

from apps.access.public import (
    AnyBranch,
    TenantAPIMixin,
    TenantAPIView,
    authorize,
    permitted_branch_ids,
)
from apps.subscriptions.public import ensure_within_limit
from common.api.pagination import SmallListPagination
from common.exceptions import PermissionDeniedError

from .. import services
from ..models import Branch, Department
from . import serializers as s

UUID_LOOKUP = r"[0-9a-f-]{36}"


# Filters are declared explicitly: django-filter's automatic filters would query the
# tenant-scoped default manager while building the class (e.g. for the API schema).
class BranchFilter(django_filters.FilterSet):
    is_active = django_filters.BooleanFilter()


class DepartmentFilter(django_filters.FilterSet):
    # Plain UUID filters: the result set is already limited to the organisation.
    branch = django_filters.UUIDFilter(field_name="branch_id")
    parent = django_filters.UUIDFilter(field_name="parent_id")


class CurrentOrganisationView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {
        "get": None,  # every member can see the organisation profile
        "patch": "organisations.organisation.update",
    }

    @extend_schema(responses={200: s.OrganisationSerializer})
    def get(self, request: Request) -> Response:
        return Response(s.OrganisationSerializer(self.ctx.organisation).data)

    @extend_schema(request=s.OrganisationSerializer, responses={200: s.OrganisationSerializer})
    def patch(self, request: Request) -> Response:
        data = s.OrganisationSerializer(self.ctx.organisation, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        organisation = services.update_organisation(
            self.ctx.organisation, dict(data.validated_data), actor_id=self.ctx.user_id
        )
        return Response(s.OrganisationSerializer(organisation).data)


class OrganisationSettingsView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {
        "get": None,
        "patch": "organisations.settings.manage",
    }

    @extend_schema(responses={200: s.OrganisationSettingsSerializer})
    def get(self, request: Request) -> Response:
        return Response(
            s.OrganisationSettingsSerializer(services.get_settings(self.ctx.organisation_id)).data
        )

    @extend_schema(
        request=s.OrganisationSettingsSerializer, responses={200: s.OrganisationSettingsSerializer}
    )
    def patch(self, request: Request) -> Response:
        settings = services.get_settings(self.ctx.organisation_id)
        data = s.OrganisationSettingsSerializer(settings, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        if data.validated_data.get("require_mfa") and not self.ctx.mfa_satisfied:
            # Prevents locking yourself (and possibly everyone) out.
            raise PermissionDeniedError(
                _("Turn on two-step verification for your own account first."),
                code="mfa_required_to_enforce",
            )
        settings = services.update_settings(
            settings, dict(data.validated_data), actor_id=self.ctx.user_id
        )
        return Response(s.OrganisationSettingsSerializer(settings).data)


class BranchViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Branch],
):
    """Branch-scoped roles see and manage only their own branches."""

    queryset = Branch.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.BranchSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = BranchFilter
    search_fields: ClassVar[list[str]] = ["name", "code", "locality"]
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": AnyBranch("organisations.branch.view"),
        "retrieve": AnyBranch("organisations.branch.view"),
        "create": "organisations.branch.manage",
        "partial_update": AnyBranch("organisations.branch.manage"),
    }

    def get_queryset(self) -> QuerySet[Branch]:
        return services.list_branches(
            branch_ids=permitted_branch_ids(self.ctx, "organisations.branch.view")
        )

    @extend_schema(request=s.BranchSerializer, responses={201: s.BranchSerializer})
    def create(self, request: Request) -> Response:
        data = s.BranchSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ensure_within_limit(
            self.ctx.entitlements, "max_branches", current=services.active_branch_count()
        )
        branch = services.create_branch(
            self.ctx.organisation, dict(data.validated_data), actor_id=self.ctx.user_id
        )
        return Response(s.BranchSerializer(branch).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.BranchSerializer, responses={200: s.BranchSerializer})
    def partial_update(self, request: Request, pk: str) -> Response:
        branch = self.get_object()
        authorize(self.ctx, "organisations.branch.manage", branch_id=branch.id)
        data = s.BranchSerializer(branch, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        if data.validated_data.get("is_active") and not branch.is_active:
            ensure_within_limit(
                self.ctx.entitlements, "max_branches", current=services.active_branch_count()
            )
        branch = services.update_branch(
            branch, dict(data.validated_data), actor_id=self.ctx.user_id
        )
        return Response(s.BranchSerializer(branch).data)


class DepartmentViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[Department],
):
    queryset = Department.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = s.DepartmentSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filterset_class = DepartmentFilter
    search_fields: ClassVar[list[str]] = ["name"]
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": AnyBranch("organisations.department.view"),
        "retrieve": AnyBranch("organisations.department.view"),
        "create": AnyBranch("organisations.department.manage"),
        "partial_update": AnyBranch("organisations.department.manage"),
        "destroy": AnyBranch("organisations.department.manage"),
    }

    def get_queryset(self) -> QuerySet[Department]:
        departments = services.list_departments()
        branch_ids = permitted_branch_ids(self.ctx, "organisations.department.view")
        if branch_ids is not None:
            departments = departments.filter(branch_id__in=branch_ids)
        return departments

    def _authorize_manage(self, branch: Branch | None) -> None:
        # Organisation-level departments need organisation-wide permission.
        authorize(
            self.ctx, "organisations.department.manage", branch_id=branch.id if branch else None
        )

    @extend_schema(request=s.DepartmentSerializer, responses={201: s.DepartmentSerializer})
    def create(self, request: Request) -> Response:
        data = s.DepartmentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        self._authorize_manage(v.get("branch"))
        department = services.create_department(
            self.ctx.organisation,
            name=v["name"],
            branch=v.get("branch"),
            parent=v.get("parent"),
            actor_id=self.ctx.user_id,
        )
        return Response(s.DepartmentSerializer(department).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=s.DepartmentSerializer, responses={200: s.DepartmentSerializer})
    def partial_update(self, request: Request, pk: str) -> Response:
        department = self.get_object()
        self._authorize_manage(department.branch)
        data = s.DepartmentSerializer(department, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        if "branch" in data.validated_data:
            self._authorize_manage(data.validated_data["branch"])
        department = services.update_department(
            department, dict(data.validated_data), actor_id=self.ctx.user_id
        )
        return Response(s.DepartmentSerializer(department).data)

    @extend_schema(responses={204: None})
    def destroy(self, request: Request, pk: str) -> Response:
        department = self.get_object()
        self._authorize_manage(department.branch)
        services.delete_department(department, actor_id=self.ctx.user_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
