from datetime import datetime
from typing import Any
from uuid import UUID

from django.db.models import QuerySet

from .models import AuditEvent


def organisation_events(
    organisation_id: UUID,
    *,
    action: str | None = None,
    actor_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    occurred_after: datetime | None = None,
    occurred_before: datetime | None = None,
) -> QuerySet[AuditEvent]:
    """Events of one organisation (row-level security also restricts reads to it)."""
    events = AuditEvent.objects.filter(organisation_id=organisation_id)
    filters: dict[str, Any] = {
        "action": action,
        "actor_id": actor_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "occurred_at__gte": occurred_after,
        "occurred_at__lt": occurred_before,
    }
    return events.filter(**{k: v for k, v in filters.items() if v is not None})
