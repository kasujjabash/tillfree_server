"""Append-only audit log enforced by PostgreSQL row-level security.

- INSERT: events of the current organisation, or events with no organisation
  (sign-in, account changes).
- SELECT: the current organisation's events, plus a user's own account events
  (no organisation) under user_context, e.g. for a "recent security activity" page.
- UPDATE / DELETE: no policy, so no rows are ever affected (FORCE applies to the owner too).
"""

from django.db import migrations

from common.tenancy.operations import setting_uuid_sql

CTX = setting_uuid_sql("app.organisation_id")
USER = setting_uuid_sql("app.user_id")

FORWARD = [
    "ALTER TABLE audit_auditevent ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE audit_auditevent FORCE ROW LEVEL SECURITY",
    "CREATE POLICY audit_read ON audit_auditevent FOR SELECT USING ("
    f"organisation_id = {CTX} OR (organisation_id IS NULL AND actor_id = {USER}))",
    "CREATE POLICY audit_append ON audit_auditevent FOR INSERT "
    f"WITH CHECK (organisation_id IS NULL OR organisation_id = {CTX})",
]
BACKWARD = [
    "DROP POLICY IF EXISTS audit_append ON audit_auditevent",
    "DROP POLICY IF EXISTS audit_read ON audit_auditevent",
    "ALTER TABLE audit_auditevent NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE audit_auditevent DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [("audit", "0001_initial")]

    operations = [migrations.RunSQL(FORWARD, BACKWARD)]
