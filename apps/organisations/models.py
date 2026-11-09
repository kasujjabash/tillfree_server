"""Organisations (tenants).

Phase 1 contains only the tenant root, because every tenant-owned table references it.
Settings, branches, departments, memberships and invitations arrive in Phase 2.

The Organisation row itself is not under row-level security: users must be able to
see the names of the organisations they belong to before choosing one. Access to it
is controlled by membership checks; sensitive organisation data lives in
tenant-owned tables that are under RLS.
"""

from typing import Any, ClassVar

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel


class Organisation(UUIDPrimaryKeyModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        CLOSED = "closed", _("Closed")

    name = models.CharField(_("name"), max_length=200)
    slug = models.SlugField(_("slug"), max_length=80, unique=True)
    status = models.CharField(
        _("status"), max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "organisations_organisation"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(status__in=["active", "suspended", "closed"]),
                name="organisations_organisation_status_valid",
            ),
        ]

    def __str__(self) -> str:
        return self.name

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        # Organisations are closed (status), not deleted. Django's PROTECT check cannot
        # see tenant rows (RLS hides them without a tenant context), so a delete would
        # fail late with an IntegrityError. Erasure will be a platform job with
        # retention rules (docs/architecture/04-data-model.md §3).
        raise OrganisationDeletionNotAllowed(
            "Organisations cannot be deleted; set status to 'closed' instead."
        )


class OrganisationDeletionNotAllowed(Exception):
    pass
