"""Typed helpers for reading configuration from environment variables.

Settings modules use these instead of ``os.environ`` directly so that a missing or
malformed value fails at startup with a clear message rather than at first use.
"""

import os

from django.core.exceptions import ImproperlyConfigured

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


def env_str(name: str, default: str | None = None) -> str:
    """Return a string variable. With no default, the variable is required."""
    value = os.environ.get(name)
    if value is None:
        if default is None:
            raise ImproperlyConfigured(f"Environment variable {name} is required.")
        return default
    return value


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalised = value.strip().lower()
    if normalised in _TRUE:
        return True
    if normalised in _FALSE:
        return False
    raise ImproperlyConfigured(f"Environment variable {name} must be a boolean, got {value!r}.")


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be an integer, got {value!r}."
        ) from exc


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ImproperlyConfigured(
            f"Environment variable {name} must be a number, got {value!r}."
        ) from exc


def env_list(name: str, default: str = "") -> list[str]:
    """Comma-separated list; blank entries are dropped."""
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]
