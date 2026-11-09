"""Removes secrets from structured log events before they are written anywhere."""

from collections.abc import MutableMapping
from typing import Any

REDACTED = "[REDACTED]"

SENSITIVE_KEY_PARTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "otp",
    "api_key",
    "apikey",
    "credential",
    "signature",
)


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: REDACTED if is_sensitive_key(str(k)) else redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(item) for item in value]
    return value


def redact_sensitive(
    _logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor. The ``event`` (message) key itself is never redacted."""
    for key in list(event_dict.keys()):
        if key == "event" or key.startswith("_"):
            continue
        event_dict[key] = REDACTED if is_sensitive_key(key) else redact(event_dict[key])
    return event_dict
