"""Notifications to members of an organisation: in-app, email and SMS.

All tables are tenant-owned (row-level security). Account emails to addresses that are
not (yet) members — verification, password reset, invitations — are sent by the
identity and access domains directly and are not stored here.
"""

from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.tenancy.models import TenantModel

CHANNEL_CHOICES = [("in_app", "In-app"), ("email", "Email"), ("sms", "SMS")]


class NotificationTemplate(TenantModel):
    """An organisation's own wording for a category/channel/language (overrides the default)."""

    category = models.CharField(max_length=100)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    locale = models.CharField(max_length=35, default="en")
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField(max_length=2000)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "notifications_template"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "category", "channel", "locale"],
                name="notif_template_unique",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.category}/{self.channel}/{self.locale}"


class NotificationPreference(TenantModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    category = models.CharField(max_length=100)
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    enabled = models.BooleanField()

    class Meta:
        db_table = "notifications_preference"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "user", "category", "channel"], name="notif_pref_unique"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.category}/{self.channel}={self.enabled}"


class Notification(TenantModel):
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+"
    )
    category = models.CharField(max_length=100)
    title = models.CharField(max_length=200)
    body = models.TextField()
    data = models.JSONField(default=dict, blank=True, help_text=_("Identifiers for deep links."))
    show_in_app = models.BooleanField(default=True)
    read_at = models.DateTimeField(null=True, blank=True)
    dedupe_key = models.CharField(max_length=100, blank=True)

    class Meta:
        db_table = "notifications_notification"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation", "recipient", "dedupe_key"],
                condition=~models.Q(dedupe_key=""),
                name="notif_dedupe",
            ),
        ]
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "recipient", "read_at"], name="notif_inbox_idx"),
        ]

    def __str__(self) -> str:
        return self.title


class NotificationDelivery(TenantModel):
    class Status(models.TextChoices):
        QUEUED = "queued", _("Queued")
        SENT = "sent", _("Sent")
        FAILED = "failed", _("Failed")

    notification = models.ForeignKey(
        Notification, on_delete=models.CASCADE, related_name="deliveries"
    )
    channel = models.CharField(max_length=10, choices=CHANNEL_CHOICES)
    destination = models.CharField(max_length=254)
    subject = models.CharField(max_length=200, blank=True)
    body = models.TextField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    provider = models.CharField(max_length=50, blank=True)
    provider_message_id = models.CharField(max_length=100, blank=True)
    last_error = models.CharField(max_length=300, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "notifications_delivery"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["organisation", "status"], name="notif_delivery_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.channel} {self.status}"
