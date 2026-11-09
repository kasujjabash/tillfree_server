from django.db import migrations

from common.tenancy.operations import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("organisations", "0001_initial")]

    operations = [
        EnableTenantRLS("OrganisationSettings"),
        EnableTenantRLS("Branch"),
        EnableTenantRLS("Department"),
    ]
