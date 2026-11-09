"""Who may do what in which organisation.

- Membership: a user belongs to an organisation (bridge table: the user can see
  their own memberships before choosing an organisation).
- Role: a custom, organisation-defined set of permissions. Ready-made roles
  (Owner, Administrator, module templates) live in code (registry.py).
- RoleAssignment: membership x (template or custom role) x optional branch.
- Invitation: an emailed offer to join with a role.
- PlatformStaff / SupportAccessGrant: platform operators and their time-boxed,
  owner-approved, read-only access to one organisation.

Design: docs/architecture/05-identity-and-access.md §6-7.
"""

from datetime import datetime
from typing import ClassVar

from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel
from common.tenancy.models import TenantModel

_ONE_ROLE_SPEC = models.Q(template_key__isnull=False, role__isnull=True) | models.Q(
    template_key__isnull=True, role__isnull=False
)


class Role(TenantModel):
    name = models.CharField(_("name"), max_length=100)
    description = models.CharField(_("description"), max_length=300, blank=True)
    permissions = ArrayField(models.CharField(max_length=100), default=list, blank=True)

    class Meta:
        db_table = "access_role"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "name"], name="access_role_name_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class Membership(TenantModel):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        LEFT = "left", _("Left")

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="memberships"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    department = models.ForeignKey(
        "organisations.Department",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="members",
    )
    joined_at = models.DateTimeField(default=timezone.now)
    left_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "access_membership"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "user"], name="access_membership_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "suspended", "left"]),
                name="access_membership_status_valid",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "status"], name="access_membership_user_idx"),
            models.Index(fields=["organisation", "status"], name="access_membership_org_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} in {self.organisation_id}"

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE


class RoleAssignment(TenantModel):
    membership = models.ForeignKey(
        Membership, on_delete=models.CASCADE, related_name="role_assignments"
    )
    template_key = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001 - NULL means 'custom role' (check constraint)
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="assignments"
    )
    branch = models.ForeignKey(
        "organisations.Branch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="role_assignments",
        help_text=_("Empty means every branch."),
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )

    class Meta:
        db_table = "access_roleassignment"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=_ONE_ROLE_SPEC, name="access_assignment_one_role"),
            models.UniqueConstraint(
                fields=["membership", "template_key", "role", "branch"],
                nulls_distinct=False,
                name="access_assignment_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.template_key or self.role_id} for {self.membership_id}"


class Invitation(TenantModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        ACCEPTED = "accepted", _("Accepted")
        REVOKED = "revoked", _("Revoked")

    email = models.EmailField(max_length=254)
    token_hash = models.CharField(max_length=64, unique=True)
    template_key = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001 - NULL means 'custom role' (check constraint)
    role = models.ForeignKey(
        Role, on_delete=models.PROTECT, null=True, blank=True, related_name="invitations"
    )
    branch = models.ForeignKey(
        "organisations.Branch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invitations",
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "access_invitation"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(condition=_ONE_ROLE_SPEC, name="access_invitation_one_role"),
            models.UniqueConstraint(
                fields=["organisation", "email"],
                condition=models.Q(status="pending"),
                name="access_invitation_one_pending",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "status"], name="access_invitation_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Invitation for {self.email}"

    def is_expired(self, now: datetime | None = None) -> bool:
        return (now or timezone.now()) >= self.expires_at


class PlatformStaff(UUIDPrimaryKeyModel, TimeStampedModel):
    """Platform operators. Being staff grants NO organisation access by itself."""

    class Role(models.TextChoices):
        SUPER_ADMIN = "super_admin", _("Super administrator")
        SUPPORT_ADMIN = "support_admin", _("Support administrator")

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="platform_staff"
    )
    role = models.CharField(max_length=20, choices=Role.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "access_platformstaff"
        verbose_name_plural = "platform staff"

    def __str__(self) -> str:
        return f"{self.user_id} ({self.role})"


class SupportAccessGrant(TenantModel):
    """Time-boxed, read-only access for one platform staff member to one organisation.

    Requested by staff, approved by the organisation (or opened as audited
    break-glass by a super administrator). Never includes sensitive or clinical data.
    """

    MAX_DURATION_MINUTES = 24 * 60

    class Status(models.TextChoices):
        REQUESTED = "requested", _("Requested")
        APPROVED = "approved", _("Approved")
        DENIED = "denied", _("Denied")
        REVOKED = "revoked", _("Revoked")

    staff_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=500)
    duration_minutes = models.PositiveIntegerField()
    break_glass = models.BooleanField(default=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.REQUESTED)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    starts_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "access_supportaccessgrant"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(duration_minutes__gte=1)
                & models.Q(duration_minutes__lte=24 * 60),
                name="access_support_duration_valid",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "status"], name="access_support_status_idx"),
        ]

    def __str__(self) -> str:
        return f"Support access for {self.staff_user_id}"

    def is_active(self, now: datetime | None = None) -> bool:
        now = now or timezone.now()
        return (
            self.status == self.Status.APPROVED
            and self.starts_at is not None
            and self.expires_at is not None
            and self.starts_at <= now < self.expires_at
        )
