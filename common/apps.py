from django.apps import AppConfig


class CommonConfig(AppConfig):
    """Framework-level code. Registered as an app only for system checks and signal wiring."""

    name = "common"
    label = "common"
    verbose_name = "Common"

    def ready(self) -> None:
        from common.observability import celery_signals  # noqa: F401  (connects signals)
        from common.tenancy import checks  # noqa: F401  (registers system checks)
