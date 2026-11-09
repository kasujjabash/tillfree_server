"""Read-only introspection used by deploy checks and tests."""

from dataclasses import dataclass

from django.apps import apps
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.models import Model

from .operations import POLICY_NAME


@dataclass(frozen=True)
class RlsStatus:
    enabled: bool
    forced: bool
    has_policy: bool

    @property
    def ok(self) -> bool:
        return self.enabled and self.forced and self.has_policy


def tenant_models() -> list[type[Model]]:
    from .models import TenantModel

    return [
        model
        for model in apps.get_models()
        if issubclass(model, TenantModel) and not model._meta.proxy
    ]


def rls_status(connection: BaseDatabaseWrapper, table: str) -> RlsStatus | None:
    """RLS state of ``table`` in the current schema, or None if the table does not exist."""
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT c.relrowsecurity, c.relforcerowsecurity,
                   EXISTS (SELECT 1 FROM pg_policies p
                           WHERE p.schemaname = n.nspname AND p.tablename = c.relname
                             AND p.policyname = %s)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s AND n.nspname = current_schema()
            """,
            [POLICY_NAME, table],
        )
        row = cursor.fetchone()
    return RlsStatus(*row) if row else None


def role_can_bypass_rls(connection: BaseDatabaseWrapper) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
        row = cursor.fetchone()
    return bool(row and row[0])
