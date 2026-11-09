"""The single place where "may this person do this?" is decided.

Scopes:
- organisation-wide: needs a grant that covers every branch (branch_id=None);
- a specific branch: a grant for every branch, or for that branch;
- anywhere: held on at least one grant (used as a gate before narrowing a list
  to the permitted branches with ``permitted_branch_ids``).

Design: docs/architecture/05-identity-and-access.md §6.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from django.utils.translation import gettext_lazy as _

from apps.identity.public import User
from apps.organisations.public import Organisation
from common.exceptions import PermissionDeniedError
from common.permissions import Sensitivity

from . import registry
from .models import Membership, SupportAccessGrant


class MFARequired(PermissionDeniedError):
    default_code = "mfa_required"
    title = _("Two-step verification is required. Sign in again with your authenticator.")


class ModuleNotEnabled(PermissionDeniedError):
    default_code = "module_not_enabled"
    title = _("This module is not enabled for the organisation.")


@dataclass(frozen=True)
class Grant:
    permissions: frozenset[str]
    branch_id: UUID | None  # None = every branch


@dataclass(frozen=True)
class AccessContext:
    """Who is acting, in which organisation, with which grants. Built once per request."""

    user: User
    organisation: Organisation
    grants: tuple[Grant, ...]
    amr: tuple[str, ...] = ()
    session_id: UUID | None = None
    require_mfa: bool = False
    ip_address: str | None = None
    membership: Membership | None = None
    support_grant: SupportAccessGrant | None = None

    @property
    def organisation_id(self) -> UUID:
        return self.organisation.id

    @property
    def user_id(self) -> UUID:
        return self.user.id

    @property
    def mfa_satisfied(self) -> bool:
        return "otp" in self.amr

    @property
    def is_support(self) -> bool:
        return self.support_grant is not None


def all_permissions(ctx: AccessContext) -> frozenset[str]:
    """Everything held anywhere (any branch)."""
    return frozenset().union(*(g.permissions for g in ctx.grants))


def organisation_wide_permissions(ctx: AccessContext) -> frozenset[str]:
    return frozenset().union(*(g.permissions for g in ctx.grants if g.branch_id is None))


def permissions_at_branch(ctx: AccessContext, branch_id: UUID) -> frozenset[str]:
    return frozenset().union(
        *(g.permissions for g in ctx.grants if g.branch_id is None or g.branch_id == branch_id)
    )


def holds(ctx: AccessContext, code: str, *, branch_id: UUID | None = None) -> bool:
    if branch_id is None:
        return code in organisation_wide_permissions(ctx)
    return code in permissions_at_branch(ctx, branch_id)


def holds_anywhere(ctx: AccessContext, code: str) -> bool:
    return code in all_permissions(ctx)


def permitted_branch_ids(ctx: AccessContext, code: str) -> set[UUID] | None:
    """None if permitted in every branch, else the set of branches where it is held."""
    if holds(ctx, code):
        return None
    return {g.branch_id for g in ctx.grants if g.branch_id and code in g.permissions}


def module_enabled(organisation_id: UUID, module: str) -> bool:
    """Subscription entitlements arrive in Phase 3; every module is enabled until then."""
    return True


def _precheck(ctx: AccessContext, code: str) -> None:
    permission = registry.get_permission(code)
    if (
        ctx.require_mfa or permission.sensitivity == Sensitivity.CLINICAL
    ) and not ctx.mfa_satisfied:
        raise MFARequired(_("Two-step verification is required for this action."))
    if not module_enabled(ctx.organisation_id, permission.module):
        raise ModuleNotEnabled(_("This module is not enabled for the organisation."))


def authorize(ctx: AccessContext, code: str, *, branch_id: UUID | None = None) -> None:
    """Require ``code`` organisation-wide (branch_id=None) or at ``branch_id``."""
    _precheck(ctx, code)
    if holds(ctx, code, branch_id=branch_id):
        return
    if holds_anywhere(ctx, code):
        raise PermissionDeniedError(
            _("You do not have this permission for this branch."), code="branch_not_permitted"
        )
    raise PermissionDeniedError(_("You do not have permission to perform this action."))


def authorize_anywhere(ctx: AccessContext, code: str) -> None:
    """Require ``code`` in at least one branch; callers then narrow by branch."""
    _precheck(ctx, code)
    if not holds_anywhere(ctx, code):
        raise PermissionDeniedError(_("You do not have permission to perform this action."))


def can_grant(ctx: AccessContext, codes: Iterable[str], *, branch_id: UUID | None) -> bool:
    """Privilege-escalation guard: you may only hand out permissions you hold yourself,
    at the scope you are granting them."""
    wanted = set(codes)
    held = (
        organisation_wide_permissions(ctx)
        if branch_id is None
        else permissions_at_branch(ctx, branch_id)
    )
    return wanted <= held


def ensure_can_grant(ctx: AccessContext, codes: Iterable[str], *, branch_id: UUID | None) -> None:
    if not can_grant(ctx, codes, branch_id=branch_id):
        raise PermissionDeniedError(
            _("You cannot grant permissions you do not hold yourself."), code="cannot_grant"
        )
