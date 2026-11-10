from django.db import migrations

from common.tenancy.operations import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("documents", "0001_initial")]

    operations = [EnableTenantRLS("FileObject")]
