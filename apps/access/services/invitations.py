"""Inviting people into an organisation.

The emailed token has the form ``<organisation id>.<secret>``. The organisation id
lets the server enter the right tenant context before looking the invitation up; the
256-bit secret is what proves possession (only its hash is stored).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.events.public import publish
from apps.identity.public import ClientInfo, IssuedTokens, User, UserManager, start_session
from apps.organisations.public import Branch
from apps.subscriptions.public import ensure_within_limit, entitlements_for
from common.exceptions import BusinessRuleViolation, ConflictError, PermissionDeniedError
from common.mail import frontend_url, queue_email
from common.tenancy.context import tenant_context, user_context
from common.tokens import hash_secret, new_secret

from .. import selectors
from ..authorization import AccessContext, ensure_can_grant
from ..models import Invitation, Membership, Role, RoleAssignment
from .members import role_spec_permissions

INVITATION_TTL = timedelta(days=7)


class InvalidInvitation(BusinessRuleViolation):
    default_code = "invalid_invitation"
    title = _("This invitation is invalid, used or expired.")


@dataclass(frozen=True)
class InvitationPreview:
    organisation_name: str
    email: str
    role_name: str
    invited_by: str
    expires_at: datetime


def _new_token(organisation_id: UUID) -> tuple[str, str]:
    secret = new_secret()
    raw = f"{organisation_id}.{secret}"
    return raw, hash_secret(raw)


def _parse(raw: str) -> UUID:
    try:
        org_part, _secret = raw.split(".", 1)
        return UUID(org_part)
    except ValueError:
        raise InvalidInvitation(_("This invitation is invalid, used or expired.")) from None


def _send(ctx_org_name: str, invitation: Invitation, raw: str, inviter: User) -> None:
    queue_email(
        to=invitation.email,
        template="access/email/invitation",
        context={
            "organisation_name": ctx_org_name,
            "inviter_name": inviter.get_full_name(),
            "role_name": selectors.role_label(
                template_key=invitation.template_key, role=invitation.role
            ),
            "url": frontend_url("invitations/accept", token=raw),
            "days": INVITATION_TTL.days,
        },
    )


@transaction.atomic
def create_invitation(
    ctx: AccessContext,
    *,
    email: str,
    template_key: str | None = None,
    role: Role | None = None,
    branch: Branch | None = None,
) -> Invitation:
    email = UserManager.normalize_email(email)
    codes = role_spec_permissions(template_key=template_key, role=role)
    ensure_can_grant(ctx, codes, branch_id=branch.id if branch else None)
    if Membership.objects.filter(user__email=email, status=Membership.Status.ACTIVE).exists():
        raise ConflictError(
            _("This person is already a member."), code="already_member", field="email"
        )
    if Invitation.objects.filter(email=email, status=Invitation.Status.PENDING).exists():
        raise ConflictError(
            _("There is already a pending invitation for this email."),
            code="invitation_pending",
            field="email",
        )
    ensure_within_limit(ctx.entitlements, "max_members", current=seats_in_use())
    raw, token_hash = _new_token(ctx.organisation_id)
    invitation = Invitation.objects.create(
        email=email,
        token_hash=token_hash,
        template_key=template_key,
        role=role,
        branch=branch,
        invited_by=ctx.user,
        expires_at=timezone.now() + INVITATION_TTL,
    )
    record(
        "invitation.created",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="invitation",
        resource_id=invitation.id,
        metadata={"template_key": template_key, "role_id": str(role.id) if role else None},
    )
    _send(ctx.organisation.name, invitation, raw, ctx.user)
    return invitation


@transaction.atomic
def resend_invitation(ctx: AccessContext, invitation: Invitation) -> Invitation:
    if invitation.status != Invitation.Status.PENDING:
        raise ConflictError(_("Only pending invitations can be resent."), code="not_pending")
    raw, token_hash = _new_token(ctx.organisation_id)
    invitation.token_hash = token_hash
    invitation.expires_at = timezone.now() + INVITATION_TTL
    invitation.save(update_fields=["token_hash", "expires_at", "updated_at"])
    record(
        "invitation.resent",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="invitation",
        resource_id=invitation.id,
    )
    _send(ctx.organisation.name, invitation, raw, ctx.user)
    return invitation


@transaction.atomic
def revoke_invitation(ctx: AccessContext, invitation: Invitation) -> Invitation:
    if invitation.status != Invitation.Status.PENDING:
        raise ConflictError(_("Only pending invitations can be revoked."), code="not_pending")
    invitation.status = Invitation.Status.REVOKED
    invitation.save(update_fields=["status", "updated_at"])
    record(
        "invitation.revoked",
        organisation_id=ctx.organisation_id,
        actor_id=ctx.user_id,
        resource_type="invitation",
        resource_id=invitation.id,
    )
    return invitation


def seats_in_use() -> int:
    """Active members plus pending invitations (each pending invitation holds a seat)."""
    active = Membership.objects.filter(status=Membership.Status.ACTIVE).count()
    pending = Invitation.objects.filter(
        status=Invitation.Status.PENDING, expires_at__gt=timezone.now()
    ).count()
    return active + pending


def _pending(raw: str) -> Invitation:
    """Look up a usable invitation. Must run inside the invitation's tenant context."""
    invitation = (
        Invitation.objects.select_for_update(of=("self",))
        .select_related("organisation", "role", "branch", "invited_by")
        .filter(token_hash=hash_secret(raw))
        .first()
    )
    if (
        invitation is None
        or invitation.status != Invitation.Status.PENDING
        or invitation.is_expired()
        or not invitation.organisation.is_active
    ):
        raise InvalidInvitation(_("This invitation is invalid, used or expired."))
    return invitation


def preview_invitation(raw: str) -> InvitationPreview:
    with tenant_context(_parse(raw)):
        invitation = _pending(raw)
        return InvitationPreview(
            organisation_name=invitation.organisation.name,
            email=invitation.email,
            role_name=selectors.role_label(
                template_key=invitation.template_key, role=invitation.role
            ),
            invited_by=invitation.invited_by.get_full_name() if invitation.invited_by else "",
            expires_at=invitation.expires_at,
        )


def _accept(invitation: Invitation, user: User) -> Membership:
    if user.email != invitation.email:
        raise PermissionDeniedError(
            _("This invitation was sent to a different email address."),
            code="invitation_email_mismatch",
        )
    membership = Membership.objects.filter(user=user).first()
    if membership is not None and membership.status == Membership.Status.ACTIVE:
        raise ConflictError(_("You are already a member."), code="already_member")
    if membership is not None and membership.status == Membership.Status.SUSPENDED:
        raise ConflictError(
            _("Your membership is suspended. Ask an administrator to reactivate it."),
            code="membership_suspended",
        )
    # The invitation already holds a seat; re-check in case the plan was lowered since.
    active = Membership.objects.filter(status=Membership.Status.ACTIVE).count()
    ensure_within_limit(entitlements_for(invitation.organisation_id), "max_members", current=active)
    now = timezone.now()
    if membership is None:
        membership = Membership.objects.create(user=user, joined_at=now)
    else:  # rejoining after leaving
        membership.status = Membership.Status.ACTIVE
        membership.joined_at, membership.left_at = now, None
        membership.save(update_fields=["status", "joined_at", "left_at", "updated_at"])
    if invitation.branch is None or invitation.branch.is_active:
        RoleAssignment.objects.create(
            membership=membership,
            template_key=invitation.template_key,
            role=invitation.role,
            branch=invitation.branch,
            granted_by=invitation.invited_by,
        )
    invitation.status = Invitation.Status.ACCEPTED
    invitation.accepted_at = now
    invitation.accepted_by = user
    invitation.save(update_fields=["status", "accepted_at", "accepted_by", "updated_at"])
    if user.email_verified_at is None:  # the emailed token proves the inbox
        user.email_verified_at = now
        user.save(update_fields=["email_verified_at", "updated_at"])
    publish(
        "access.member_joined",
        organisation_id=invitation.organisation_id,
        payload={"membership_id": str(membership.id)},
    )
    record(
        "invitation.accepted",
        organisation_id=invitation.organisation_id,
        actor_id=user.id,
        resource_type="invitation",
        resource_id=invitation.id,
        metadata={"membership_id": str(membership.id)},
    )
    return membership


@transaction.atomic
def accept_invitation(user: User, raw: str) -> Membership:
    organisation_id = _parse(raw)
    with tenant_context(organisation_id), user_context(user.id):
        return _accept(_pending(raw), user)


def accept_invitation_as_new_user(
    raw: str, *, full_name: str, password: str, client: ClientInfo
) -> IssuedTokens:
    """Create the invitee's account (email already proven) and sign them in."""
    organisation_id = _parse(raw)
    with transaction.atomic():
        with tenant_context(organisation_id):
            invitation = _pending(raw)
            email = invitation.email
        if User.objects.filter(email=email).exists():
            raise ConflictError(
                _("An account with this email exists. Sign in to accept the invitation."),
                code="account_exists",
            )
        validate_password(password, user=User(email=email, full_name=full_name))
        user = User.objects.create_user(email, password, full_name=full_name)
        with tenant_context(organisation_id), user_context(user.id):
            _accept(_pending(raw), user)
        return start_session(user, amr=["pwd"], client=client)
