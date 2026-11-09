"""The audit domain's interface for other domains."""

from .models import AuditEvent
from .services import record

__all__ = ["AuditEvent", "record"]
