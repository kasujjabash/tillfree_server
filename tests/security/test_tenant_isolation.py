"""Tenant isolation foundation (docs/architecture/03-multi-tenancy.md §8).

Endpoint-level cross-tenant tests are added in Phase 2 when tenant endpoints exist.
These tests prove the layers underneath: the manager, the save guard, PostgreSQL RLS,
transaction-local context, and background tasks.
"""

import re
from pathlib import Path

import pytest
from django.contrib import admin
from django.db import ProgrammingError, connection, transaction

from common.tenancy.context import get_current_organisation_id, tenant_context
from common.tenancy.exceptions import CrossTenantWrite, TenantContextConflict, TenantContextMissing
from common.tenancy.inspection import rls_status, role_can_bypass_rls, tenant_models
from common.tenancy.models import TenantModel
from tests.tenancy_app.models import Widget
from tests.tenancy_app.tasks import count_widgets

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parents[2]


def _db_setting() -> str | None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('app.organisation_id', true)")
        return cursor.fetchone()[0]


@pytest.fixture
def widgets(org_a, org_b):
    with tenant_context(org_a.id):
        Widget.objects.create(name="a1")
        Widget.objects.create(name="a2")
    with tenant_context(org_b.id):
        Widget.objects.create(name="b1")


# --------------------------------------------------------------- application layer
def test_manager_raises_without_tenant_context(db):
    with pytest.raises(TenantContextMissing):
        Widget.objects.all()


def test_manager_returns_only_current_tenant_rows(widgets, org_a, org_b):
    with tenant_context(org_a.id):
        assert sorted(Widget.objects.values_list("name", flat=True)) == ["a1", "a2"]
    with tenant_context(org_b.id):
        assert list(Widget.objects.values_list("name", flat=True)) == ["b1"]


def test_lookup_by_id_of_other_tenant_finds_nothing(widgets, org_a, org_b):
    with tenant_context(org_b.id):
        b_id = Widget.objects.get().id
    with tenant_context(org_a.id):
        assert not Widget.objects.filter(id=b_id).exists()


def test_save_fills_organisation_from_context(org_a):
    with tenant_context(org_a.id):
        widget = Widget.objects.create(name="x")
    assert widget.organisation_id == org_a.id


def test_save_without_context_is_refused(org_a):
    with pytest.raises(TenantContextMissing):
        Widget(name="x").save()


def test_save_into_other_tenant_is_refused(org_a, org_b):
    with tenant_context(org_a.id), pytest.raises(CrossTenantWrite):
        Widget(name="x", organisation=org_b).save()


def test_switching_tenant_inside_context_is_refused(org_a, org_b):
    with tenant_context(org_a.id), pytest.raises(TenantContextConflict):  # noqa: SIM117
        with tenant_context(org_b.id):
            pass


def test_reentering_same_tenant_is_allowed(org_a):
    with tenant_context(org_a.id), tenant_context(org_a.id):
        assert get_current_organisation_id() == org_a.id


# --------------------------------------------------------------- database layer (RLS)
def test_rls_hides_other_tenants_even_from_unscoped_queries(widgets, org_a):
    with tenant_context(org_a.id):
        assert Widget.unscoped.count() == 2


def test_rls_returns_no_rows_without_context(widgets):
    with transaction.atomic():
        assert Widget.unscoped.count() == 0


def test_rls_blocks_insert_into_other_tenant(org_a, org_b):
    # bulk_create skips Model.save(), so only RLS stands in the way.
    with tenant_context(org_a.id), pytest.raises(ProgrammingError, match="row-level security"):  # noqa: SIM117
        with transaction.atomic():
            Widget.unscoped.bulk_create([Widget(name="x", organisation=org_b)])


def test_rls_blocks_moving_rows_to_other_tenant(widgets, org_a, org_b):
    with tenant_context(org_a.id), pytest.raises(ProgrammingError, match="row-level security"):  # noqa: SIM117
        with transaction.atomic():
            Widget.unscoped.update(organisation=org_b)


def test_context_is_cleared_after_nested_block(widgets, org_a):
    # pytest-django wraps each test in a transaction, so this context is a savepoint.
    with tenant_context(org_a.id):
        assert _db_setting() == str(org_a.id)
    assert _db_setting() == ""
    assert Widget.unscoped.count() == 0


@pytest.mark.django_db(transaction=True)
def test_context_does_not_survive_on_a_reused_connection(org_a):
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        pid_before = cursor.fetchone()[0]

    with tenant_context(org_a.id):
        Widget.objects.create(name="committed")

    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_backend_pid()")
        assert cursor.fetchone()[0] == pid_before  # same database session
    assert _db_setting() in ("", None)
    assert Widget.unscoped.count() == 0  # autocommit, no context
    with transaction.atomic():
        assert Widget.unscoped.count() == 0


def test_application_role_cannot_bypass_rls(db):
    assert not role_can_bypass_rls(connection)


def test_every_tenant_model_has_rls_enabled_forced_and_policed(db):
    models = tenant_models()
    assert Widget in models
    for model in models:
        status = rls_status(connection, model._meta.db_table)
        assert status is not None and status.ok, model._meta.label


# --------------------------------------------------------------- background tasks
def test_tenant_task_runs_in_the_given_tenant(widgets, org_a, org_b):
    assert count_widgets.delay(organisation_id=str(org_a.id)).get() == 2
    assert count_widgets.delay(organisation_id=str(org_b.id)).get() == 1


@pytest.mark.parametrize("organisation_id", ["", None])
def test_tenant_task_without_organisation_is_refused(db, organisation_id):
    with pytest.raises(TenantContextMissing):
        count_widgets.delay(organisation_id=organisation_id)


# --------------------------------------------------------------- guardrails
def test_no_tenant_model_is_registered_in_django_admin():
    registered = [m for m in admin.site._registry if issubclass(m, TenantModel)]
    assert registered == []


UNSCOPED_ALLOWLIST: set[str] = set()  # e.g. {"apps/audit/exporter.py"} once justified


def test_unscoped_manager_is_used_only_in_allowlisted_modules():
    pattern = re.compile(r"\.unscoped\b")
    offenders = [
        str(path.relative_to(ROOT))
        for package in ("apps", "common")
        for path in (ROOT / package).rglob("*.py")
        if "tests" not in path.parts
        and path.name != "models.py"
        and pattern.search(path.read_text())
        and str(path.relative_to(ROOT)) not in UNSCOPED_ALLOWLIST
    ]
    assert offenders == []
