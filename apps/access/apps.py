from django.apps import AppConfig


class AccessConfig(AppConfig):
    name = "apps.access"
    label = "access"
    verbose_name = "Access control"

    def ready(self) -> None:
        from . import registry

        registry.load()
