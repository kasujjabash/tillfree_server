from typing import Any
from uuid import UUID

import structlog

from common.observability.middleware import get_request_id
from common.observability.redaction import redact

from .models import AuditEvent

logger = structlog.get_logger("tillfree.audit")


def record(
    action: str,
    *,
    organisation_id: UUID | None = None,
    actor_id: UUID | None = None,
    actor_type: str | None = None,
    resource_type: str = "",
    resource_id: object = "",
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditEvent:
    """Write one audit event in the caller's transaction.

    ``metadata`` must hold identifiers and facts, never secrets or personal payloads;
    keys that look secret are redacted as a safety net.
    """
    if actor_type is None:
        actor_type = AuditEvent.ActorType.USER if actor_id else AuditEvent.ActorType.ANONYMOUS
    event = AuditEvent.objects.create(
        organisation_id=organisation_id,
        actor_id=actor_id,
        actor_type=actor_type,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id or ""),
        metadata=redact(metadata or {}),
        request_id=(get_request_id() or "")[:64],
        ip_address=ip_address,
    )
    logger.info("audit_event", action=action, resource_type=resource_type)
    return event
