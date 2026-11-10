from django.apps import AppConfig


class EventsConfig(AppConfig):
    name = "apps.events"
    label = "events"
    verbose_name = "Domain events (outbox)"

    def ready(self) -> None:
        from . import registry

        registry.load()
