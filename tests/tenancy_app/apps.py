from django.apps import AppConfig


class TenancyAppConfig(AppConfig):
    """Test-only app with a tenant-owned model, installed by config.settings.testing."""

    name = "tests.tenancy_app"
    label = "tenancy_app"
