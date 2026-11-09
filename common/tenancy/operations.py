"""Migration operation that puts a tenant table under Row-Level Security.

Usage in a migration, after the table is created::

    from common.tenancy.operations import EnableTenantRLS

    operations = [
        migrations.CreateModel(name="Branch", ...),
        EnableTenantRLS("Branch"),
        # Bridge table: members of the organisation, plus the user on their own rows.
        EnableTenantRLS("Membership", user_column="user_id"),
    ]
"""

from typing import Any

from django.db.backends.base.schema import BaseDatabaseSchemaEditor
from django.db.migrations.operations.base import Operation
from django.db.migrations.state import ProjectState

from .context import DB_SETTING, DB_USER_SETTING

POLICY_NAME = "tenant_isolation"


def setting_uuid_sql(setting: str) -> str:
    # NULL when never set on this connection; '' after a previous transaction set it
    # locally. Both mean "not set" and match no rows (fail closed).
    return f"NULLIF(current_setting('{setting}', true), '')::uuid"


class EnableTenantRLS(Operation):
    reversible = True
    reduces_to_sql = True

    def __init__(
        self, model_name: str, column: str = "organisation_id", user_column: str | None = None
    ) -> None:
        self.model_name = model_name
        self.column = column
        self.user_column = user_column

    def deconstruct(self) -> tuple[str, list[Any], dict[str, Any]]:
        kwargs: dict[str, Any] = {}
        if self.column != "organisation_id":
            kwargs["column"] = self.column
        if self.user_column:
            kwargs["user_column"] = self.user_column
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
        org_condition = f"{schema_editor.quote_name(self.column)} = {setting_uuid_sql(DB_SETTING)}"
        read_condition = org_condition
        if self.user_column:
            user_col = schema_editor.quote_name(self.user_column)
            read_condition = (
                f"({org_condition}) OR {user_col} = {setting_uuid_sql(DB_USER_SETTING)}"
            )
        schema_editor.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        schema_editor.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        # Writes always require the organisation context, even on bridge tables.
        schema_editor.execute(
            f"CREATE POLICY {POLICY_NAME} ON {table} "
            f"USING ({read_condition}) WITH CHECK ({org_condition})"
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
