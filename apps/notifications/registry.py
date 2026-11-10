"""Collects ``CATEGORIES`` from each app's ``notifications.py`` (like permissions)."""

from importlib import import_module

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured

from common.notifications import NotificationCategory

_categories: dict[str, NotificationCategory] = {}


def load() -> None:
    _categories.clear()
    for config in apps.get_app_configs():
        try:
            module = import_module(f"{config.name}.notifications")
        except ModuleNotFoundError as exc:
            if exc.name == f"{config.name}.notifications":
                continue
            raise
        for category in getattr(module, "CATEGORIES", []):
            if not category.key.startswith(f"{config.label}."):
                raise ImproperlyConfigured(
                    f"{config.label} declares category {category.key} for another app."
                )
            if category.key in _categories:
                raise ImproperlyConfigured(
                    f"Notification category {category.key} is declared twice."
                )
            _categories[category.key] = category


def get(key: str) -> NotificationCategory:
    try:
        return _categories[key]
    except KeyError:
        raise ImproperlyConfigured(f"Unknown notification category {key!r}") from None


def all_categories() -> list[NotificationCategory]:
    return sorted(_categories.values(), key=lambda c: c.key)
