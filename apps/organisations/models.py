"""Organisations (tenants), their settings, branches and departments.

The Organisation row itself is not under row-level security: users must be able to
see the names of the organisations they belong to before choosing one. Access to it
is controlled by membership checks; everything else here is tenant-owned and under RLS.
"""

from typing import Any, ClassVar

from django.db import models
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel
from common.tenancy.models import TenantModel
from common.validators import (
    validate_country_code,
    validate_currency_code,
    validate_e164_phone,
    validate_timezone,
)


class OrganisationDeletionNotAllowed(Exception):
    pass


class Organisation(UUIDPrimaryKeyModel, TimeStampedModel):
    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        SUSPENDED = "suspended", _("Suspended")
        CLOSED = "closed", _("Closed")

    name = models.CharField(_("name"), max_length=200)
    legal_name = models.CharField(_("legal name"), max_length=200, blank=True)
    slug = models.SlugField(_("slug"), max_length=80, unique=True)
    # Reference-table foreign keys replace these codes in Phase 3 (localisation).
    country_code = models.CharField(
        _("country"), max_length=2, validators=[validate_country_code], help_text="ISO 3166-1"
    )
    base_currency = models.CharField(
        _("base currency"), max_length=3, validators=[validate_currency_code], help_text="ISO 4217"
    )
    default_timezone = models.CharField(
        _("default timezone"), max_length=64, validators=[validate_timezone]
    )
    default_locale = models.CharField(_("default language"), max_length=35, default="en")
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

    @property
    def is_active(self) -> bool:
        return self.status == self.Status.ACTIVE

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        # Organisations are closed (status), not deleted. Django's PROTECT check cannot
        # see tenant rows (RLS hides them without a tenant context), so a delete would
        # fail late with an IntegrityError. Erasure will be a platform job with
        # retention rules (docs/architecture/04-data-model.md §3).
        raise OrganisationDeletionNotAllowed(
            "Organisations cannot be deleted; set status to 'closed' instead."
        )


class OrganisationSettings(TenantModel):
    require_mfa = models.BooleanField(
        _("require two-step verification"),
        default=False,
        help_text=_("Members must sign in with two-step verification to use this organisation."),
    )
    financial_year_start_month = models.PositiveSmallIntegerField(default=1)

    class Meta:
        db_table = "organisations_organisationsettings"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation"], name="organisations_settings_one_per_org"
            ),
            models.CheckConstraint(
                condition=models.Q(financial_year_start_month__gte=1)
                & models.Q(financial_year_start_month__lte=12),
                name="organisations_settings_fy_month_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"Settings for {self.organisation_id}"


class Branch(TenantModel):
    name = models.CharField(_("name"), max_length=150)
    code = models.CharField(_("code"), max_length=30, blank=True)
    country_code = models.CharField(max_length=2, validators=[validate_country_code])
    timezone = models.CharField(max_length=64, validators=[validate_timezone])
    phone = models.CharField(max_length=16, blank=True, validators=[validate_e164_phone])
    address_line1 = models.CharField(max_length=200, blank=True)
    address_line2 = models.CharField(max_length=200, blank=True)
    locality = models.CharField(max_length=100, blank=True)
    region = models.CharField(max_length=100, blank=True)
    postal_code = models.CharField(max_length=20, blank=True)
    directions = models.CharField(
        max_length=300, blank=True, help_text=_("Landmarks and directions to find the branch.")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "organisations_branch"
        verbose_name_plural = "branches"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "name"], name="organisations_branch_name_unique"
            ),
            models.UniqueConstraint(
                fields=["organisation", "code"],
                condition=~models.Q(code=""),
                name="organisations_branch_code_unique",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "is_active"], name="org_branch_active_idx"),
        ]

    def __str__(self) -> str:
        return self.name


class Department(TenantModel):
    name = models.CharField(_("name"), max_length=150)
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="departments"
    )
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )

    class Meta:
        db_table = "organisations_department"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "parent", "name"],
                nulls_distinct=False,
                name="organisations_department_name_unique",
            ),
        ]

    def __str__(self) -> str:
        return self.name
