"""Read-side queries for the access domain."""

from typing import Any
from uuid import UUID

from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from apps.identity.public import User

from . import registry
from .authorization import Grant
from .models import (
    Invitation,
    Membership,
    PlatformStaff,
    Role,
    RoleAssignment,
    SupportAccessGrant,
)


def assignment_permissions(assignment: RoleAssignment) -> frozenset[str]:
    if assignment.template_key:
        return registry.template_permissions(assignment.template_key)
    if assignment.role is None:
        return frozenset()
    # Permissions removed from code are ignored rather than granting something unknown.
    return frozenset(p for p in assignment.role.permissions if registry.is_known(p))


def grants_for(membership: Membership) -> tuple[Grant, ...]:
    assignments = RoleAssignment.objects.filter(membership=membership).select_related("role")
    return tuple(Grant(assignment_permissions(a), a.branch_id) for a in assignments)


def membership_permissions(membership: Membership) -> frozenset[str]:
    return frozenset().union(*(g.permissions for g in grants_for(membership)))


def my_memberships(user: User) -> list[Membership]:
    """The user's active memberships across organisations. Runs under user_context:
    row-level security exposes only the user's own rows (bridge policy)."""
    return list(
        Membership.unscoped.filter(user=user, status=Membership.Status.ACTIVE)
        .select_related("organisation")
        .order_by("organisation__name")
    )


def membership_for_user(user: User) -> Membership | None:
    """The user's membership in the current organisation (tenant context)."""
    return Membership.objects.select_related("organisation").filter(user=user).first()


def list_memberships() -> QuerySet[Membership]:
    return Membership.objects.select_related("user", "department").prefetch_related(
        Prefetch("role_assignments", queryset=RoleAssignment.objects.select_related("role"))
    )


def get_membership(membership_id: UUID | str) -> Membership | None:
    return list_memberships().filter(pk=membership_id).first()


def active_owner_memberships() -> QuerySet[Membership]:
    return Membership.objects.filter(
        status=Membership.Status.ACTIVE, role_assignments__template_key=registry.OWNER
    ).distinct()


def owner_emails() -> list[str]:
    return list(active_owner_memberships().values_list("user__email", flat=True))


def list_roles() -> QuerySet[Role]:
    return Role.objects.order_by("name")


def get_role(role_id: UUID | str) -> Role | None:
    return Role.objects.filter(pk=role_id).first()


def list_assignments(*, membership_id: UUID | str | None = None) -> QuerySet[RoleAssignment]:
    assignments = RoleAssignment.objects.select_related("role", "branch", "membership")
    if membership_id:
        assignments = assignments.filter(membership_id=membership_id)
    return assignments


def get_assignment(assignment_id: UUID | str) -> RoleAssignment | None:
    return list_assignments().filter(pk=assignment_id).first()


def list_invitations(*, status: str | None = None) -> QuerySet[Invitation]:
    invitations = Invitation.objects.select_related("role", "branch", "invited_by")
    return invitations.filter(status=status) if status else invitations


def get_invitation(invitation_id: UUID | str) -> Invitation | None:
    return list_invitations().filter(pk=invitation_id).first()


def platform_staff(user: User) -> PlatformStaff | None:
    return PlatformStaff.objects.filter(user=user, is_active=True).first()


def active_support_grant(user: User) -> SupportAccessGrant | None:
    """The user's approved, current grant for the organisation in context."""
    now = timezone.now()
    return (
        SupportAccessGrant.objects.filter(
            staff_user=user,
            status=SupportAccessGrant.Status.APPROVED,
            starts_at__lte=now,
            expires_at__gt=now,
        )
        .order_by("-expires_at")
        .first()
    )


def list_support_grants(*, status: str | None = None) -> QuerySet[SupportAccessGrant]:
    grants = SupportAccessGrant.objects.select_related("staff_user", "decided_by")
    return grants.filter(status=status) if status else grants


def get_support_grant(grant_id: UUID | str) -> SupportAccessGrant | None:
    return list_support_grants().filter(pk=grant_id).first()


def my_support_grants(user: User) -> list[SupportAccessGrant]:
    """Staff's own grants across organisations (bridge policy on staff_user_id)."""
    return list(
        SupportAccessGrant.unscoped.filter(staff_user=user)
        .select_related("organisation")
        .order_by("-created_at")
    )


def role_label(*, template_key: str | None, role: Role | None) -> str:
    if template_key:
        return registry.template_name(template_key)
    return role.name if role else ""


def assignment_summary(membership: Membership) -> list[dict[str, Any]]:
    return [
        {
            "id": a.id,
            "template_key": a.template_key,
            "role_id": a.role_id,
            "role_name": role_label(template_key=a.template_key, role=a.role),
            "branch_id": a.branch_id,
        }
        for a in membership.role_assignments.all()
    ]
