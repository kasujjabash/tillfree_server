from django.db import migrations

from common.tenancy.operations import EnableTenantRLS


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]

    operations = [
        EnableTenantRLS("NotificationTemplate"),
        EnableTenantRLS("NotificationPreference"),
        EnableTenantRLS("Notification"),
        EnableTenantRLS("NotificationDelivery"),
    ]
