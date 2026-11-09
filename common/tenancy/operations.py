"""Migration operation that puts a tenant table under Row-Level Security.

Usage in a migration, after the table is created::

    from common.tenancy.operations import EnableTenantRLS

    operations = [
        migrations.CreateModel(name="Branch", ...),
        EnableTenantRLS("Branch"),
    ]
"""

from typing import Any

from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ProjectState

from .context import DB_SETTING

POLICY_NAME = "tenant_isolation"


def _current_org_sql() -> str:
    # NULL when never set on this connection; '' after a previous transaction set it
    # locally. Both mean "no tenant" and match no rows (fail closed).
    return f"NULLIF(current_setting('{DB_SETTING}', true), '')::uuid"


class EnableTenantRLS(Operation):
    reversible = True
    reduces_to_sql = True

    def __init__(self, model_name: str, column: str = "organisation_id") -> None:
        self.model_name = model_name
        self.column = column

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        kwargs = {} if self.column == "organisation_id" else {"column": self.column}
        return (self.__class__.__qualname__, [self.model_name], kwargs)

    def state_forwards(self, app_label: str, state: ProjectState) -> None:
        pass  # database-only change

    def _table(
        self, app_label: str, schema_editor: BaseDatabaseSchemaEditor, state: ProjectState
    ) -> str | None:
        model = state.apps.get_model(app_label, self.model_name)
        if not self.allow_migrate_model(schema_editor.connection.alias, model):
            return None
        return schema_editor.quote_name(model._meta.db_table)

    def database_forwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        table = self._table(app_label, schema_editor, to_state)
        if table is None:
            return
        column = schema_editor.quote_name(self.column)
        condition = f"{column} = {_current_org_sql()}"
        schema_editor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        schema_editor.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} USING ({condition}) WITH CHECK ({condition})"
        )

    def database_backwards(
        self,
        app_label: str,
        schema_editor: BaseDatabaseSchemaEditor,
        from_state: ProjectState,
        to_state: ProjectState,
    ) -> None:
        table = self._table(app_label, schema_editor, from_state)
        if table is None:
            return
        schema_editor.execute(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        schema_editor.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    def describe(self) -> str:
        return f"Enable tenant row-level security on {self.model_name}"

    @property
    def migration_name_fragment(self) -> str:
        return f"{self.model_name.lower()}_tenant_rls"
