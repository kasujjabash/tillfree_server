"""Time-boxed, read-only support access (docs/architecture/05-identity-and-access.md §7).

1. Platform staff request access to one organisation with a reason and duration.
2. Someone in the organisation holding ``access.support.manage`` approves or denies.
   A super administrator may instead open "break-glass" access for an incident; it is
   approved immediately, and every Owner is emailed at once.
3. While active, staff can make read-only requests with standard (non-sensitive,
   non-clinical) view permissions, with two-step verification, and every request is
   audited. The organisation can revoke the grant at any time.
"""

from datetime import timedelta
from uuid import UUID

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import AuditEvent, record
from apps.events.public import publish
from apps.identity.public import User
from apps.organisations.public import get_organisation
from common.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from common.tenancy.context import tenant_context

from .. import selectors
from ..authorization import AccessContext
from ..models import PlatformStaff, SupportAccessGrant


def _require_staff(user: User) -> PlatformStaff:
    staff = selectors.platform_staff(user)
    if staff is None:
        raise PermissionDeniedError(_("Platform staff only."), code="not_platform_staff")
    return staff


def _approve(grant: SupportAccessGrant, *, decided_by: User | None) -> None:
    now = timezone.now()
    grant.status = SupportAccessGrant.Status.APPROVED
    grant.decided_by, grant.decided_at = decided_by, now
    grant.starts_at = now
    grant.expires_at = now + timedelta(minutes=grant.duration_minutes)
    grant.save()


@transaction.atomic
def request_access(
    staff_user: User,
    *,
    organisation_id: UUID,
    reason: str,
    duration_minutes: int,
    break_glass: bool = False,
) -> SupportAccessGrant:
    staff = _require_staff(staff_user)
    if break_glass and staff.role != PlatformStaff.Role.SUPER_ADMIN:
        raise PermissionDeniedError(
            _("Only super administrators can open break-glass access."), code="break_glass_denied"
        )
    organisation = get_organisation(organisation_id)
    if organisation is None:
        raise NotFoundError(_("Organisation not found."))
    with tenant_context(organisation.id):
        grant = SupportAccessGrant(
            staff_user=staff_user,
            reason=reason,
            duration_minutes=duration_minutes,
            break_glass=break_glass,
        )
        grant.full_clean(exclude=["organisation"])
        grant.save()
        if break_glass:
            _approve(grant, decided_by=None)
        record(
            "support.break_glass_opened" if break_glass else "support.access_requested",
            organisation_id=organisation.id,
            actor_id=staff_user.id,
            actor_type=AuditEvent.ActorType.PLATFORM_STAFF,
            resource_type="support_grant",
            resource_id=grant.id,
            metadata={"duration_minutes": duration_minutes, "reason": reason},
        )
        publish(
            "access.support_access",
            organisation_id=organisation.id,
            payload={"grant_id": str(grant.id)},
        )
    return grant


@transaction.atomic
def decide(ctx: AccessContext, grant: SupportAccessGrant, *, approve: bool) -> SupportAccessGrant:
    if grant.status != SupportAccessGrant.Status.REQUESTED:
        raise ConflictError(_("This request has already been decided."), code="already_decided")
    if approve:
        _approve(grant, decided_by=ctx.user)
    else:
        grant.status = SupportAccessGrant.Status.DENIED
        grant.decided_by, grant.decided_at = ctx.user, timezone.now()
        grant.save()
    record(
        "support.access_approved" if approve else "support.access_denied",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="support_grant",
        resource_id=grant.id,
    )
    return grant


@transaction.atomic
def revoke(ctx: AccessContext, grant: SupportAccessGrant) -> SupportAccessGrant:
    if grant.status not in (
        SupportAccessGrant.Status.REQUESTED,
        SupportAccessGrant.Status.APPROVED,
    ):
        raise ConflictError(_("This access has already ended."), code="already_ended")
    grant.status = SupportAccessGrant.Status.REVOKED
    grant.decided_by = grant.decided_by or ctx.user
    grant.expires_at = min(grant.expires_at or timezone.now(), timezone.now())
    grant.save()
    record(
        "support.access_revoked",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="support_grant",
        resource_id=grant.id,
    )
    return grant


def record_support_request(ctx: AccessContext, *, method: str, path: str) -> None:
    """Every request made under support access is audited."""
    if ctx.support_grant is None:
        return
    record(
        "support.request",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        actor_type=AuditEvent.ActorType.PLATFORM_STAFF,
        resource_type="support_grant",
        resource_id=ctx.support_grant.id,
        metadata={"method": method, "path": path},
        ip_address=ctx.ip_address,
    )
