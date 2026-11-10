"""Transactional outbox (docs/architecture/02-project-structure.md §4).

These are internal infrastructure tables: never exposed through the API, read by the
relay across organisations, and holding identifiers only (payloads are validated).
They are therefore not under row-level security; the handlers that act on an event run
inside the event's tenant context.
"""

from typing import ClassVar

from django.db import models
from django.utils import timezone

from common.db.models import UUIDPrimaryKeyModel


class OutboxEvent(UUIDPrimaryKeyModel):
    organisation_id = models.UUIDField(null=True, blank=True)
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    occurred_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "events_outboxevent"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(
                fields=["occurred_at"],
                condition=models.Q(published_at__isnull=True),
                name="events_unpublished_idx",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.event_type} {self.id}"


class ProcessedEvent(models.Model):
    """Written in the same transaction as a handler's effects: each handler runs once per event."""

    event_id = models.UUIDField()
    handler = models.CharField(max_length=200)
    processed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "events_processedevent"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["event_id", "handler"], name="events_processed_once"),
        ]

    def __str__(self) -> str:
        return f"{self.handler} handled {self.event_id}"
