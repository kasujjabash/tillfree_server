"""Builds the AccessContext for a request inside an organisation's tenant context."""

from uuid import UUID

from django.utils.translation import gettext_lazy as _

from apps.identity.public import AuthInfo, User
from apps.organisations.public import get_organisation, get_settings
from common.exceptions import NotFoundError, PermissionDeniedError

from . import registry, selectors
from .authorization import AccessContext, Grant


def build_access_context(
    *, user: User, organisation_id: UUID, auth: AuthInfo | None, ip_address: str | None
) -> AccessContext:
    """Resolve membership (or an approved support grant) for ``organisation_id``.

    Must run inside ``tenant_context(organisation_id)``. Anyone who is neither an active
    member nor holding a current support grant gets 404, so the endpoint does not reveal
    whether the organisation exists.
    """
    amr = auth.amr if auth else ()
    session_id = auth.session_id if auth else None

    membership = selectors.membership_for_user(user)
    if membership is not None and membership.is_active:
        organisation = membership.organisation
        if not organisation.is_active:
            raise PermissionDeniedError(
                _("This organisation is not active."), code="organisation_inactive"
            )
        settings = get_settings(organisation.id)
        return AccessContext(
            user=user,
            organisation=organisation,
            grants=selectors.grants_for(membership),
            amr=amr,
            session_id=session_id,
            require_mfa=settings.require_mfa,
            ip_address=ip_address,
            membership=membership,
        )

    support_grant = selectors.active_support_grant(user) if selectors.platform_staff(user) else None
    supported = get_organisation(organisation_id) if support_grant else None
    if support_grant is None or supported is None:
        raise NotFoundError(_("Organisation not found."))
    return AccessContext(
        user=user,
        organisation=supported,
        grants=(Grant(registry.read_only_permissions(), None),),
        amr=amr,
        session_id=session_id,
        require_mfa=True,  # support access always needs two-step verification
        ip_address=ip_address,
        support_grant=support_grant,
    )
