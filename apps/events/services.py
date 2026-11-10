from typing import Any
from uuid import UUID

from django.db import transaction

from .models import OutboxEvent

_SCALARS = (str, int, bool, type(None))


def _validate_payload(payload: dict[str, Any]) -> None:
    """Identifiers and simple facts only: no nested objects, no free text blobs."""
    for key, value in payload.items():
        items = value if isinstance(value, list) else [value]
        if not all(isinstance(item, _SCALARS) for item in items):
            raise TypeError(f"Event payload field {key!r} must be a scalar or a list of scalars.")
        if any(isinstance(item, str) and len(item) > 200 for item in items):
            raise ValueError(f"Event payload field {key!r} is too long; send identifiers only.")


def publish(
    event_type: str, *, organisation_id: UUID | None, payload: dict[str, Any]
) -> OutboxEvent:
    """Record an event in the caller's transaction; it is relayed after commit."""
    _validate_payload(payload)
    event = OutboxEvent.objects.create(
        organisation_id=organisation_id, event_type=event_type, payload=payload
    )

    def trigger_relay() -> None:
        from .tasks import relay_outbox

        relay_outbox.delay()

    # robust: if the queue is down, the periodic relay picks the event up later.
    transaction.on_commit(trigger_relay, robust=True)
    return event
