"""Event handlers are declared in each domain's ``handlers.py``::

    from apps.events.public import on_event

    @on_event("access.member_joined")
    def notify_admins(event: DomainEvent) -> None: ...

Handlers run in a Celery task, inside the event's tenant context, at least once; the
outbox records each (event, handler) pair so the handler's database effects happen once.
"""

from collections.abc import Callable
from importlib import import_module

from django.apps import apps

from common.events import DomainEvent

Handler = Callable[[DomainEvent], None]
_handlers: dict[str, dict[str, Handler]] = {}


def on_event(event_type: str) -> Callable[[Handler], Handler]:
    def register(func: Handler) -> Handler:
        name = f"{func.__module__}.{func.__qualname__}"
        _handlers.setdefault(event_type, {})[name] = func
        return func

    return register


def handlers_for(event_type: str) -> dict[str, Handler]:
    return dict(_handlers.get(event_type, {}))


def get_handler(name: str) -> Handler | None:
    for handlers in _handlers.values():
        if name in handlers:
            return handlers[name]
    return None


def load() -> None:
    for config in apps.get_app_configs():
        try:
            import_module(f"{config.name}.handlers")
        except ModuleNotFoundError as exc:
            if exc.name != f"{config.name}.handlers":
                raise
