"""Organisation, settings, branch and department operations.

Callers (the API layer, other domains) are responsible for authorisation; services
enforce the business rules and record audit events. Every function that touches
tenant data expects to run inside ``tenant_context`` for that organisation.
"""

import secrets
from typing import Any
from uuid import UUID

from django.db import transaction
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.localisation.public import ensure_country_enabled, ensure_currency_active
from common.exceptions import BusinessRuleViolation

from .models import Branch, Department, Organisation, OrganisationSettings

ORGANISATION_EDITABLE = ("name", "legal_name", "default_timezone", "default_locale")
SETTINGS_EDITABLE = ("require_mfa", "financial_year_start_month")
BRANCH_EDITABLE = (
    "name",
    "code",
    "country_code",
    "timezone",
    "phone",
    "address_line1",
    "address_line2",
    "locality",
    "region",
    "postal_code",
    "directions",
    "is_active",
)


def _unique_slug(name: str) -> str:
    base = slugify(name)[:60] or "organisation"
    slug = base
    while Organisation.objects.filter(slug=slug).exists():
        slug = f"{base}-{secrets.token_hex(3)}"
    return slug


def _apply(instance: Any, changes: dict[str, Any], allowed: tuple[str, ...]) -> list[str]:
    changed = [f for f in allowed if f in changes and getattr(instance, f) != changes[f]]
    for field in changed:
        setattr(instance, field, changes[field])
    return changed


# ------------------------------------------------------------------ organisations
def create_organisation_record(
    *,
    name: str,
    country_code: str,
    base_currency: str = "",
    default_timezone: str = "",
    default_locale: str = "",
    legal_name: str = "",
) -> Organisation:
    """Create the tenant root. Call ``initialise_organisation`` inside its tenant context.

    The country must be enabled on the platform. Currency, timezone and language default
    to the country's defaults; nothing is assumed about any particular country.
    """
    country = ensure_country_enabled(country_code)
    base_currency = base_currency or country.default_currency_id
    ensure_currency_active(base_currency)
    default_timezone = default_timezone or country.default_timezone
    default_locale = default_locale or (country.languages[0] if country.languages else "en")
    organisation = Organisation(
        name=name,
        legal_name=legal_name,
        slug=_unique_slug(name),
        country_code=country_code,
        base_currency=base_currency,
        default_timezone=default_timezone,
        default_locale=default_locale,
    )
    organisation.full_clean()
    organisation.save()
    return organisation


def initialise_organisation(organisation: Organisation) -> OrganisationSettings:
    """Tenant-owned records every organisation starts with."""
    return OrganisationSettings.objects.create(organisation=organisation)


@transaction.atomic
def update_organisation(
    organisation: Organisation, changes: dict[str, Any], *, actor_id: UUID
) -> Organisation:
    changed = _apply(organisation, changes, ORGANISATION_EDITABLE)
    if changed:
        organisation.full_clean()
        organisation.save()
        record(
            "organisation.updated",
            organisation_id=organisation.id,
            actor_id=actor_id,
            resource_type="organisation",
            resource_id=organisation.id,
            metadata={"fields": changed},
        )
    return organisation


def list_organisations() -> Any:
    """Platform view of all organisations (the tenant root is not under RLS)."""
    return Organisation.objects.order_by("name")


def get_organisation(organisation_id: UUID | str) -> Organisation | None:
    return Organisation.objects.filter(pk=organisation_id).first()


def get_settings(organisation_id: UUID) -> OrganisationSettings:
    return OrganisationSettings.objects.get(organisation_id=organisation_id)


@transaction.atomic
def update_settings(
    settings: OrganisationSettings, changes: dict[str, Any], *, actor_id: UUID
) -> OrganisationSettings:
    before = {f: getattr(settings, f) for f in SETTINGS_EDITABLE}
    changed = _apply(settings, changes, SETTINGS_EDITABLE)
    if changed:
        settings.full_clean()
        settings.save()
        record(
            "organisation.settings_updated",
            organisation_id=settings.organisation_id,
            actor_id=actor_id,
            resource_type="organisation_settings",
            resource_id=settings.id,
            metadata={f: {"from": before[f], "to": getattr(settings, f)} for f in changed},
        )
    return settings


# ------------------------------------------------------------------ branches
@transaction.atomic
def create_branch(organisation: Organisation, data: dict[str, Any], *, actor_id: UUID) -> Branch:
    values = {f: data[f] for f in BRANCH_EDITABLE if f in data}
    values.setdefault("country_code", organisation.country_code)
    values.setdefault("timezone", organisation.default_timezone)
    ensure_country_enabled(values["country_code"])
    branch = Branch(organisation=organisation, **values)
    branch.full_clean()
    branch.save()
    record(
        "branch.created",
        organisation_id=organisation.id,
        actor_id=actor_id,
        resource_type="branch",
        resource_id=branch.id,
    )
    return branch


@transaction.atomic
def update_branch(branch: Branch, changes: dict[str, Any], *, actor_id: UUID) -> Branch:
    changed = _apply(branch, changes, BRANCH_EDITABLE)
    if "country_code" in changed:
        ensure_country_enabled(branch.country_code)
    if changed:
        branch.full_clean()
        branch.save()
        record(
            "branch.updated",
            organisation_id=branch.organisation_id,
            actor_id=actor_id,
            resource_type="branch",
            resource_id=branch.id,
            metadata={"fields": changed},
        )
    return branch


def list_branches(*, branch_ids: set[UUID] | None = None) -> Any:
    """Branches of the current organisation; ``branch_ids`` limits to permitted ones."""
    branches = Branch.objects.order_by("name")
    if branch_ids is not None:
        branches = branches.filter(id__in=branch_ids)
    return branches


def active_branch_count() -> int:
    return Branch.objects.filter(is_active=True).count()


def get_branch(branch_id: UUID | str) -> Branch | None:
    return Branch.objects.filter(pk=branch_id).first()


# ------------------------------------------------------------------ departments
def _check_department_links(department: Department) -> None:
    ancestor = department.parent
    while ancestor is not None:
        if ancestor.pk == department.pk:
            raise BusinessRuleViolation(
                _("A department cannot be inside itself."), code="department_cycle", field="parent"
            )
        ancestor = ancestor.parent


@transaction.atomic
def create_department(
    organisation: Organisation,
    *,
    name: str,
    branch: Branch | None,
    parent: Department | None,
    actor_id: UUID,
) -> Department:
    department = Department(organisation=organisation, name=name, branch=branch, parent=parent)
    department.full_clean()
    department.save()
    record(
        "department.created",
        organisation_id=organisation.id,
        actor_id=actor_id,
        resource_type="department",
        resource_id=department.id,
    )
    return department


@transaction.atomic
def update_department(
    department: Department, changes: dict[str, Any], *, actor_id: UUID
) -> Department:
    changed = _apply(department, changes, ("name", "branch", "parent"))
    if changed:
        _check_department_links(department)
        department.full_clean()
        department.save()
        record(
            "department.updated",
            organisation_id=department.organisation_id,
            actor_id=actor_id,
            resource_type="department",
            resource_id=department.id,
            metadata={"fields": changed},
        )
    return department


@transaction.atomic
def delete_department(department: Department, *, actor_id: UUID) -> None:
    department_id, organisation_id = department.id, department.organisation_id
    department.delete()  # PROTECT on children/branch -> 409 resource_in_use
    record(
        "department.deleted",
        organisation_id=organisation_id,
        actor_id=actor_id,
        resource_type="department",
        resource_id=department_id,
    )


def list_departments() -> Any:
    return Department.objects.select_related("branch", "parent").order_by("name")


def get_department(department_id: UUID | str) -> Department | None:
    return Department.objects.filter(pk=department_id).first()
