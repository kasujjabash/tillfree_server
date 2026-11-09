import pytest
from django.core.management import call_command
from django.db import connection

from common.tenancy.checks import check_database_role, check_tenant_tables_have_rls
from common.tenancy.inspection import rls_status

pytestmark = pytest.mark.security


def test_deploy_checks_pass_on_a_correctly_configured_database(db):
    assert check_database_role(databases=["default"]) == []
    assert check_tenant_tables_have_rls(databases=["default"]) == []


def test_deploy_checks_skip_when_no_database_is_requested():
    assert check_tenant_tables_have_rls(databases=None) == []


def test_deploy_check_reports_tenant_table_without_rls(db):
    with connection.cursor() as cursor:
        cursor.execute("ALTER TABLE tenancy_app_widget NO FORCE ROW LEVEL SECURITY")
    errors = check_tenant_tables_have_rls(databases=["default"])
    assert [e.id for e in errors] == ["tenancy.E002"]


@pytest.mark.django_db(transaction=True)
def test_rls_migration_is_reversible():
    call_command("migrate", "tenancy_app", "zero", verbosity=0)
    assert rls_status(connection, "tenancy_app_widget") is None
    call_command("migrate", "tenancy_app", verbosity=0)
    status = rls_status(connection, "tenancy_app_widget")
    assert status is not None and status.ok
