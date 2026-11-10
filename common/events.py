"""Domain event types shared by every domain (the outbox lives in apps.events)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class DomainEvent:
    """Something that happened. Payloads carry identifiers and facts only, never personal
    data or secrets: handlers load what they need inside the right tenant context."""

    id: UUID
    type: str
    organisation_id: UUID | None
    payload: dict[str, Any]
    occurred_at: datetime
