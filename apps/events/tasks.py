from uuid import UUID

import structlog
from celery import shared_task
from django.db import IntegrityError, transaction
from django.utils import timezone

from common.events import DomainEvent
from common.tenancy.context import tenant_context

from .models import OutboxEvent, ProcessedEvent
from .registry import get_handler, handlers_for

logger = structlog.get_logger("tillfree.events")

BATCH_SIZE = 100


@shared_task
def relay_outbox() -> int:
    """Hand unpublished events to their handlers. Runs after each commit that published
    an event, and periodically as a backstop. Safe to run concurrently (SKIP LOCKED)."""
    relayed = 0
    with transaction.atomic():
        events = list(
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(published_at__isnull=True)
            .order_by("occurred_at")[:BATCH_SIZE]
        )
        for event in events:
            event.attempts += 1
            for name in handlers_for(event.event_type):
                run_handler.delay(
                    event_id=str(event.id),
                    handler=name,
                    organisation_id=str(event.organisation_id) if event.organisation_id else None,
                )
            event.published_at = timezone.now()
            event.save(update_fields=["published_at", "attempts"])
            relayed += 1
    return relayed


@shared_task(
    autoretry_for=(Exception,),
    dont_autoretry_for=(LookupError,),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=8,
)
def run_handler(*, event_id: str, handler: str, organisation_id: str | None) -> str:
    func = get_handler(handler)
    if func is None:
        raise LookupError(f"No handler named {handler}")
    event_row = OutboxEvent.objects.get(pk=event_id)
    event = DomainEvent(
        id=event_row.id,
        type=event_row.event_type,
        organisation_id=event_row.organisation_id,
        payload=event_row.payload,
        occurred_at=event_row.occurred_at,
    )
    if organisation_id:
        with tenant_context(UUID(organisation_id)):
            return _run_once(func, event, handler)
    with transaction.atomic():
        return _run_once(func, event, handler)


def _run_once(func: object, event: DomainEvent, handler: str) -> str:
    if ProcessedEvent.objects.filter(event_id=event.id, handler=handler).exists():
        return "already_processed"
    try:
        with transaction.atomic():
            ProcessedEvent.objects.create(event_id=event.id, handler=handler)
            func(event)  # type: ignore[operator]
    except IntegrityError:
        return "already_processed"  # a concurrent run won
    logger.info("event_handled", event_type=event.type, handler=handler)
    return "processed"
