from typing import Any

from rest_framework import serializers

from common.api.fields import TenantRelatedField

from ..models import Branch, Department, Organisation, OrganisationSettings


class OrganisationSerializer(serializers.ModelSerializer[Organisation]):
    class Meta:
        model = Organisation
        fields = (
            "id",
            "name",
            "legal_name",
            "slug",
            "country_code",
            "base_currency",
            "default_timezone",
            "default_locale",
            "status",
            "created_at",
        )
        # Country and currency are fixed after creation: transactions depend on them.
        read_only_fields = ("id", "slug", "country_code", "base_currency", "status", "created_at")
        extra_kwargs: dict[str, Any] = {"default_timezone": {"validators": []}}


class OrganisationSettingsSerializer(serializers.ModelSerializer[OrganisationSettings]):
    class Meta:
        model = OrganisationSettings
        fields = ("require_mfa", "financial_year_start_month", "updated_at")
        read_only_fields = ("updated_at",)


class BranchSerializer(serializers.ModelSerializer[Branch]):
    class Meta:
        model = Branch
        fields = (
            "id",
            "name",
            "code",
            "country_code",
            "timezone",
            "phone",
            "address_line1",
            "address_line2",
            "locality",
            "region",
            "postal_code",
            "directions",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs: dict[str, Any] = {
            "country_code": {"required": False},
            "timezone": {"required": False},
        }
        validators: list[Any] = []  # constraints validated by the service (full_clean)


class DepartmentSerializer(serializers.ModelSerializer[Department]):
    branch = TenantRelatedField(Branch, required=False, allow_null=True)
    parent = TenantRelatedField(Department, required=False, allow_null=True)  # type: ignore[assignment]

    class Meta:
        model = Department
        fields = ("id", "name", "branch", "parent", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
        validators: list[Any] = []
