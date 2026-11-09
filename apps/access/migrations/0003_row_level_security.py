from django.db import migrations

from common.tenancy.operations import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("access", "0002_initial")]

    operations = [
        EnableTenantRLS("Role"),
        # Bridge tables: a user also sees their own rows without organisation context.
        EnableTenantRLS("Membership", user_column="user_id"),
        EnableTenantRLS("SupportAccessGrant", user_column="staff_user_id"),
        EnableTenantRLS("RoleAssignment"),
        EnableTenantRLS("Invitation"),
    ]
