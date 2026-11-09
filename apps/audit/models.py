"""Append-only audit trail (docs/architecture/07-security.md §3).

Row-level policies (see the migration) make the table append-only for the
application: rows can be inserted and read by their organisation, never updated or
deleted. Events without an organisation (sign-in, account changes) are readable by
the user they concern (under ``user_context``) and by platform tooling.

Actor and organisation are stored as plain UUIDs, not foreign keys, so the trail is
independent of other domains and survives user pseudonymisation.
"""

from typing import ClassVar

from django.db import models
from django.utils import timezone

from common.db.models import UUIDPrimaryKeyModel


class AuditEvent(UUIDPrimaryKeyModel):
    class ActorType(models.TextChoices):
        USER = "user", "User"
        PLATFORM_STAFF = "platform_staff", "Platform staff"
        SYSTEM = "system", "System"
        ANONYMOUS = "anonymous", "Anonymous"

    occurred_at = models.DateTimeField(default=timezone.now, editable=False)
    organisation_id = models.UUIDField(null=True, blank=True, editable=False)
    actor_id = models.UUIDField(null=True, blank=True, editable=False)
    actor_type = models.CharField(max_length=20, choices=ActorType.choices, editable=False)
    action = models.CharField(max_length=100, editable=False)
    resource_type = models.CharField(max_length=100, blank=True, editable=False)
    resource_id = models.CharField(max_length=64, blank=True, editable=False)
    metadata = models.JSONField(default=dict, blank=True, editable=False)
    request_id = models.CharField(max_length=64, blank=True, editable=False)
    ip_address = models.GenericIPAddressField(null=True, blank=True, editable=False)

    class Meta:
        db_table = "audit_auditevent"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation_id", "occurred_at"], name="audit_org_time_idx"),
            models.Index(fields=["organisation_id", "action"], name="audit_org_action_idx"),
            models.Index(fields=["actor_id", "occurred_at"], name="audit_actor_time_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.occurred_at:%Y-%m-%d %H:%M:%S} {self.action}"
