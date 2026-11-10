"""The events domain's interface: publish events and declare handlers."""

from common.events import DomainEvent

from .registry import on_event
from .services import publish

__all__ = ["DomainEvent", "on_event", "publish"]
