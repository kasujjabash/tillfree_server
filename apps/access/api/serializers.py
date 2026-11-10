from typing import Any

from rest_framework import serializers

from apps.organisations.public import Branch, Department, Organisation
from common.api.fields import TenantRelatedField

from .. import registry, selectors
from ..models import Invitation, Membership, Role, RoleAssignment, SupportAccessGrant


class OrganisationCreateSerializer(serializers.Serializer[Any]):
    name = serializers.CharField(max_length=200)
    legal_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")
    country_code = serializers.CharField(max_length=2, help_text="ISO 3166-1 alpha-2, e.g. UG")
    base_currency = serializers.CharField(
        max_length=3, required=False, help_text="ISO 4217; defaults to the country's currency"
    )
    default_timezone = serializers.CharField(
        max_length=64, required=False, help_text="IANA; defaults to the country's timezone"
    )
    default_locale = serializers.CharField(
        max_length=35, required=False, help_text="BCP 47; defaults to the country's first language"
    )


class OrganisationSummarySerializer(serializers.ModelSerializer[Organisation]):
    class Meta:
        model = Organisation
        fields = ("id", "name", "slug", "status")


class MyMembershipSerializer(serializers.ModelSerializer[Membership]):
    organisation = OrganisationSummarySerializer(read_only=True)

    class Meta:
        model = Membership
        fields = ("id", "organisation", "status", "joined_at")


class PermissionSerializer(serializers.Serializer[Any]):
    code = serializers.CharField()
    module = serializers.CharField()
    description = serializers.CharField()
    sensitivity = serializers.CharField()


class RoleTemplateSerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    permissions = serializers.ListField(child=serializers.CharField())


class GrantSerializer(serializers.Serializer[Any]):
    branch_id = serializers.UUIDField(allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField())


class MyAccessSerializer(serializers.Serializer[Any]):
    """What the signed-in user may do in the current organisation (drives the UI)."""

    organisation = OrganisationSummarySerializer()
    membership_id = serializers.UUIDField(allow_null=True)
    permissions = serializers.ListField(child=serializers.CharField())
    grants = GrantSerializer(many=True)
    mfa_satisfied = serializers.BooleanField()
    require_mfa = serializers.BooleanField()
    support_access = serializers.BooleanField()


class RoleSerializer(serializers.ModelSerializer[Role]):
    permissions = serializers.ListField(
        child=serializers.CharField(max_length=100), allow_empty=True
    )

    class Meta:
        model = Role
        fields = ("id", "name", "description", "permissions", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
        validators: list[Any] = []  # uniqueness enforced by the service (model validation)


class AssignmentSerializer(serializers.ModelSerializer[RoleAssignment]):
    role_name = serializers.SerializerMethodField()

    class Meta:
        model = RoleAssignment
        fields = ("id", "membership", "template_key", "role", "role_name", "branch", "created_at")

    def get_role_name(self, obj: RoleAssignment) -> str:
        return selectors.role_label(template_key=obj.template_key, role=obj.role)


class RoleSpecSerializer(serializers.Serializer[Any]):
    """Either a template key or a custom role, optionally limited to one branch."""

    template_key = serializers.CharField(
        max_length=50, required=False, allow_null=True, default=None
    )
    role = TenantRelatedField(Role, required=False, allow_null=True, default=None)
    branch = TenantRelatedField(Branch, required=False, allow_null=True, default=None)

    def validate_template_key(self, value: str | None) -> str | None:
        if value is not None and not registry.is_template(value):
            raise serializers.ValidationError("Unknown role template.", code="invalid_choice")
        return value

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if (attrs.get("template_key") is None) == (attrs.get("role") is None):
            raise serializers.ValidationError(
                "Give exactly one of template_key or role.", code="invalid_role"
            )
        return attrs


class AssignmentCreateSerializer(RoleSpecSerializer):
    membership = TenantRelatedField(Membership)


class MembershipSerializer(serializers.ModelSerializer[Membership]):
    user_id = serializers.UUIDField(source="user.id", read_only=True)
    email = serializers.EmailField(source="user.email", read_only=True)
    full_name = serializers.CharField(source="user.full_name", read_only=True)
    department = TenantRelatedField(Department, allow_null=True, required=False)
    roles = serializers.SerializerMethodField()

    class Meta:
        model = Membership
        fields = (
            "id",
            "user_id",
            "email",
            "full_name",
            "status",
            "department",
            "roles",
            "joined_at",
            "left_at",
        )
        read_only_fields = ("id", "status", "joined_at", "left_at")

    def get_roles(self, obj: Membership) -> list[dict[str, Any]]:
        return selectors.assignment_summary(obj)


class InvitationSerializer(serializers.ModelSerializer[Invitation]):
    role_name = serializers.SerializerMethodField()
    invited_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Invitation
        fields = (
            "id",
            "email",
            "template_key",
            "role",
            "role_name",
            "branch",
            "status",
            "invited_by_name",
            "expires_at",
            "accepted_at",
            "created_at",
        )

    def get_role_name(self, obj: Invitation) -> str:
        return selectors.role_label(template_key=obj.template_key, role=obj.role)

    def get_invited_by_name(self, obj: Invitation) -> str:
        return obj.invited_by.get_full_name() if obj.invited_by else ""


class InvitationCreateSerializer(RoleSpecSerializer):
    email = serializers.EmailField(max_length=254)


class InvitationTokenSerializer(serializers.Serializer[Any]):
    token = serializers.CharField(max_length=200)


class InvitationAcceptNewSerializer(InvitationTokenSerializer):
    full_name = serializers.CharField(max_length=200)
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    device_label = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    use_cookie = serializers.BooleanField(required=False, default=False)


class InvitationPreviewSerializer(serializers.Serializer[Any]):
    organisation_name = serializers.CharField()
    email = serializers.EmailField()
    role_name = serializers.CharField()
    invited_by = serializers.CharField()
    expires_at = serializers.DateTimeField()


class SupportGrantSerializer(serializers.ModelSerializer[SupportAccessGrant]):
    staff_name = serializers.CharField(source="staff_user.get_full_name", read_only=True)
    organisation_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = SupportAccessGrant
        fields = (
            "id",
            "organisation_id",
            "staff_name",
            "reason",
            "duration_minutes",
            "break_glass",
            "status",
            "decided_at",
            "starts_at",
            "expires_at",
            "created_at",
        )


class SupportRequestSerializer(serializers.Serializer[Any]):
    organisation_id = serializers.UUIDField()
    reason = serializers.CharField(max_length=500)
    duration_minutes = serializers.IntegerField(min_value=1, max_value=24 * 60)
    break_glass = serializers.BooleanField(required=False, default=False)


class PlatformOrganisationSerializer(serializers.ModelSerializer[Organisation]):
    class Meta:
        model = Organisation
        fields = ("id", "name", "slug", "country_code", "status", "created_at")
