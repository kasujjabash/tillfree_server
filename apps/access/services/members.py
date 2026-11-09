"""Managing members, custom roles and role assignments.

Guards (docs/architecture/05-identity-and-access.md §6):
- you can grant only permissions you hold, at the scope you grant them;
- you can manage (suspend, remove, change roles of) only members whose permissions
  are a subset of yours, never yourself through the admin endpoints;
- an organisation always keeps at least one active Owner;
- grants that include sensitive or clinical permissions are audited and emailed to
  every Owner.
"""

from collections.abc import Iterable
from typing import Any

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.organisations.public import Branch, Department
from common.exceptions import BusinessRuleViolation, ConflictError, PermissionDeniedError
from common.mail import queue_email
from common.permissions import Sensitivity

from .. import registry, selectors
from ..authorization import AccessContext, all_permissions, ensure_can_grant
from ..models import Membership, Role, RoleAssignment


# ------------------------------------------------------------------ helpers
def _sensitive(codes: Iterable[str]) -> list[str]:
    return sorted(
        c
        for c in codes
        if registry.is_known(c) and registry.get_permission(c).sensitivity != Sensitivity.STANDARD
    )


def role_spec_permissions(*, template_key: str | None, role: Role | None) -> frozenset[str]:
    if (template_key is None) == (role is None):
        raise BusinessRuleViolation(
            _("Choose either a role template or a custom role."), code="invalid_role"
        )
    if template_key is not None:
        if not registry.is_template(template_key):
            raise BusinessRuleViolation(
                _("Unknown role template."), code="invalid_role", field="template_key"
            )
        return registry.template_permissions(template_key)
    if role is None:  # pragma: no cover - excluded by the check above
        return frozenset()
    return frozenset(p for p in role.permissions if registry.is_known(p))


def _ensure_can_manage(ctx: AccessContext, target: Membership) -> None:
    if ctx.membership is not None and target.pk == ctx.membership.pk:
        raise PermissionDeniedError(
            _("You cannot change your own access here."), code="cannot_manage_self"
        )
    if not selectors.membership_permissions(target) <= all_permissions(ctx):
        raise PermissionDeniedError(
            _("This member has permissions you do not hold."), code="cannot_manage_member"
        )


def _would_leave_no_owner(
    *, membership: Membership | None = None, assignment: RoleAssignment | None = None
) -> bool:
    owners = selectors.active_owner_memberships()
    if membership is not None:
        owners = owners.exclude(pk=membership.pk)
    if assignment is not None and assignment.template_key == registry.OWNER:
        others = RoleAssignment.objects.filter(
            membership=assignment.membership, template_key=registry.OWNER
        ).exclude(pk=assignment.pk)
        if not others.exists():
            owners = owners.exclude(pk=assignment.membership_id)
    return not owners.exists()


def _ensure_owner_remains(**kwargs: Any) -> None:
    if _would_leave_no_owner(**kwargs):
        raise ConflictError(
            _("The organisation must keep at least one active Owner."), code="last_owner"
        )


def _notify_owners_of_sensitive_grant(
    ctx: AccessContext, *, member_email: str, role_name: str, sensitive: list[str]
) -> None:
    queue_email(
        to=selectors.owner_emails(),
        template="access/email/sensitive_role_granted",
        context={
            "organisation": ctx.organisation,
            "granted_by": ctx.user,
            "member_email": member_email,
            "role_name": role_name,
            "permissions": sensitive,
        },
    )


# ------------------------------------------------------------------ memberships
@transaction.atomic
def update_membership(
    ctx: AccessContext, membership: Membership, *, department: Department | None
) -> Membership:
    if membership.department_id != (department.id if department else None):
        membership.department = department
        membership.save(update_fields=["department", "updated_at"])
        record(
            "membership.updated",
            organisation_id=ctx.organisation_id,
            actor_id=ctx.user_id,
            resource_type="membership",
            resource_id=membership.id,
            metadata={"department_id": str(department.id) if department else None},
        )
    return membership


@transaction.atomic
def suspend_membership(ctx: AccessContext, membership: Membership) -> Membership:
    _ensure_can_manage(ctx, membership)
    if membership.status != Membership.Status.ACTIVE:
        raise ConflictError(_("Only active members can be suspended."), code="not_active")
    _ensure_owner_remains(membership=membership)
    membership.status = Membership.Status.SUSPENDED
    membership.save(update_fields=["status", "updated_at"])
    record(
        "membership.suspended",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="membership",
        resource_id=membership.id,
    )
    return membership


@transaction.atomic
def reactivate_membership(ctx: AccessContext, membership: Membership) -> Membership:
    _ensure_can_manage(ctx, membership)
    if membership.status != Membership.Status.SUSPENDED:
        raise ConflictError(_("Only suspended members can be reactivated."), code="not_suspended")
    membership.status = Membership.Status.ACTIVE
    membership.save(update_fields=["status", "updated_at"])
    record(
        "membership.reactivated",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="membership",
        resource_id=membership.id,
    )
    return membership


def _end_membership(ctx: AccessContext, membership: Membership, action: str) -> None:
    if membership.status == Membership.Status.LEFT:
        raise ConflictError(_("This person is no longer a member."), code="not_a_member")
    _ensure_owner_remains(membership=membership)
    RoleAssignment.objects.filter(membership=membership).delete()
    membership.status = Membership.Status.LEFT
    membership.left_at = timezone.now()
    membership.department = None
    membership.save(update_fields=["status", "left_at", "department", "updated_at"])
    record(
        action,
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="membership",
        resource_id=membership.id,
    )


@transaction.atomic
def remove_membership(ctx: AccessContext, membership: Membership) -> None:
    _ensure_can_manage(ctx, membership)
    _end_membership(ctx, membership, "membership.removed")


@transaction.atomic
def leave_organisation(ctx: AccessContext) -> None:
    if ctx.membership is None:
        raise PermissionDeniedError(_("You are not a member of this organisation."))
    _end_membership(ctx, ctx.membership, "membership.left")


# ------------------------------------------------------------------ custom roles
def _validate_codes(codes: Iterable[str]) -> list[str]:
    codes = sorted(set(codes))
    unknown = [c for c in codes if not registry.is_known(c)]
    if unknown:
        raise BusinessRuleViolation(
            _("Unknown permissions: %(codes)s") % {"codes": ", ".join(unknown)},
            code="unknown_permission",
            field="permissions",
        )
    return codes


@transaction.atomic
def create_role(
    ctx: AccessContext, *, name: str, permissions: Iterable[str], description: str = ""
) -> Role:
    codes = _validate_codes(permissions)
    ensure_can_grant(ctx, codes, branch_id=None)
    role = Role(
        organisation_id=ctx.organisation_id, name=name, description=description, permissions=codes
    )
    role.full_clean()
    role.save()
    record(
        "role.created",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="role",
        resource_id=role.id,
        metadata={"permissions": codes, "sensitive": _sensitive(codes)},
    )
    return role


@transaction.atomic
def update_role(ctx: AccessContext, role: Role, changes: dict[str, Any]) -> Role:
    old_codes = set(role.permissions)
    # Editing a role changes everyone who holds it: you must hold everything in it,
    # before and after the change.
    ensure_can_grant(ctx, old_codes, branch_id=None)
    if "permissions" in changes:
        new_codes = _validate_codes(changes["permissions"])
        ensure_can_grant(ctx, new_codes, branch_id=None)
        role.permissions = new_codes
    for field in ("name", "description"):
        if field in changes:
            setattr(role, field, changes[field])
    role.full_clean()
    role.save()
    added = sorted(set(role.permissions) - old_codes)
    record(
        "role.updated",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="role",
        resource_id=role.id,
        metadata={
            "added": added,
            "removed": sorted(old_codes - set(role.permissions)),
            "sensitive_added": _sensitive(added),
        },
    )
    sensitive_added = _sensitive(added)
    if sensitive_added and role.assignments.exists():
        _notify_owners_of_sensitive_grant(
            ctx, member_email="", role_name=role.name, sensitive=sensitive_added
        )
    return role


@transaction.atomic
def delete_role(ctx: AccessContext, role: Role) -> None:
    if role.assignments.exists() or role.invitations.filter(status="pending").exists():
        raise ConflictError(
            _("Remove this role from members and pending invitations first."), code="role_in_use"
        )
    ensure_can_grant(ctx, role.permissions, branch_id=None)
    role_id = role.id
    role.invitations.all().delete()
    role.delete()
    record(
        "role.deleted",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="role",
        resource_id=role_id,
    )


# ------------------------------------------------------------------ assignments
@transaction.atomic
def assign_role(
    ctx: AccessContext,
    membership: Membership,
    *,
    template_key: str | None = None,
    role: Role | None = None,
    branch: Branch | None = None,
) -> RoleAssignment:
    if membership.status != Membership.Status.ACTIVE:
        raise ConflictError(_("Roles can only be given to active members."), code="not_active")
    if branch is not None and not branch.is_active:
        raise BusinessRuleViolation(
            _("This branch is inactive."), code="branch_inactive", field="branch"
        )
    codes = role_spec_permissions(template_key=template_key, role=role)
    ensure_can_grant(ctx, codes, branch_id=branch.id if branch else None)
    if template_key == registry.OWNER and branch is not None:
        raise BusinessRuleViolation(
            _("The Owner role always covers every branch."), code="owner_not_branch_scoped"
        )
    exists = RoleAssignment.objects.filter(
        membership=membership, template_key=template_key, role=role, branch=branch
    ).exists()
    if exists:
        raise ConflictError(_("The member already has this role here."), code="already_assigned")
    assignment = RoleAssignment.objects.create(
        membership=membership,
        template_key=template_key,
        role=role,
        branch=branch,
        granted_by=ctx.user,
    )
    sensitive = _sensitive(codes)
    record(
        "role.assigned",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="role_assignment",
        resource_id=assignment.id,
        metadata={
            "membership_id": str(membership.id),
            "template_key": template_key,
            "role_id": str(role.id) if role else None,
            "branch_id": str(branch.id) if branch else None,
            "sensitive": sensitive,
        },
    )
    if sensitive:
        _notify_owners_of_sensitive_grant(
            ctx,
            member_email=membership.user.email,
            role_name=selectors.role_label(template_key=template_key, role=role),
            sensitive=sensitive,
        )
    return assignment


@transaction.atomic
def remove_assignment(ctx: AccessContext, assignment: RoleAssignment) -> None:
    membership = assignment.membership
    removing_own_role = ctx.membership is not None and membership.pk == ctx.membership.pk
    if not removing_own_role:
        _ensure_can_manage(ctx, membership)
    codes = role_spec_permissions(template_key=assignment.template_key, role=assignment.role)
    ensure_can_grant(ctx, codes, branch_id=assignment.branch_id)
    _ensure_owner_remains(assignment=assignment)
    assignment_id = assignment.id
    assignment.delete()
    record(
        "role.unassigned",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="role_assignment",
        resource_id=assignment_id,
        metadata={"membership_id": str(membership.id), "template_key": assignment.template_key},
    )
