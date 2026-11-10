"""Creating an organisation: the creator becomes its first Owner."""

from typing import Any

from django.db import transaction
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.identity.public import User
from apps.organisations.public import (
    Organisation,
    create_organisation_record,
    initialise_organisation,
)
from apps.subscriptions.public import start_trial
from common.exceptions import PermissionDeniedError
from common.tenancy.context import tenant_context, user_context

from .. import registry
from ..models import Membership, RoleAssignment

CREATE_FIELDS = (
    "name",
    "legal_name",
    "country_code",
    "base_currency",
    "default_timezone",
    "default_locale",
)


@transaction.atomic
def create_organisation(
    user: User, data: dict[str, Any], *, ip_address: str | None = None
) -> Organisation:
    if not user.email_verified:
        raise PermissionDeniedError(
            _("Verify your email address before creating an organisation."),
            code="email_not_verified",
        )
    organisation = create_organisation_record(**{f: data[f] for f in CREATE_FIELDS if f in data})
    with tenant_context(organisation.id), user_context(user.id):
        initialise_organisation(organisation)
        start_trial(organisation)
        membership = Membership.objects.create(user=user)
        RoleAssignment.objects.create(
            membership=membership, template_key=registry.OWNER, granted_by=user
        )
        record(
            "organisation.created",
            organisation_id=organisation.id,
            actor_id=user.id,
            resource_type="organisation",
            resource_id=organisation.id,
            ip_address=ip_address,
        )
    return organisation
