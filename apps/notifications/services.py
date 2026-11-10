"""Sending notifications to members, the in-app inbox, preferences and templates.

``notify`` runs inside the organisation's tenant context (it is normally called from an
event handler). It records one Notification per recipient and queues a delivery per
external channel; deliveries are sent by a Celery task with retries.
"""

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from apps.identity.public import User
from apps.organisations.public import get_organisation
from common.exceptions import BusinessRuleViolation, NotFoundError
from common.notifications import CHANNELS, ChannelTemplate, NotificationCategory

from . import registry, rendering
from .models import Notification, NotificationDelivery, NotificationPreference, NotificationTemplate

EXTERNAL_CHANNELS = ("email", "sms")


# ------------------------------------------------------------------ preferences
def enabled_channels(user: User, category: NotificationCategory) -> set[str]:
    channels = set(category.default_channels)
    if category.mandatory:
        return channels
    for pref in NotificationPreference.objects.filter(user=user, category=category.key):
        if pref.channel not in category.templates:
            continue
        (channels.add if pref.enabled else channels.discard)(pref.channel)
    if category.sensitive:
        channels.discard("sms")
    return channels


def preferences_for(user: User) -> list[dict[str, Any]]:
    rows = []
    for category in registry.all_categories():
        enabled = enabled_channels(user, category)
        for channel in CHANNELS:
            if channel not in category.templates or (channel == "sms" and category.sensitive):
                continue
            rows.append(
                {
                    "category": category.key,
                    "description": category.description,
                    "channel": channel,
                    "enabled": channel in enabled,
                    "mandatory": category.mandatory,
                }
            )
    return rows


@transaction.atomic
def set_preferences(user: User, changes: Iterable[dict[str, Any]]) -> None:
    for change in changes:
        category = registry.get(change["category"])
        channel = change["channel"]
        if channel not in category.templates:
            raise BusinessRuleViolation(
                _("This channel is not available for the category."), code="invalid_channel"
            )
        if category.mandatory:
            raise BusinessRuleViolation(
                _("This notification cannot be switched off."), code="mandatory_notification"
            )
        NotificationPreference.objects.update_or_create(
            user=user,
            category=category.key,
            channel=channel,
            defaults={"enabled": bool(change["enabled"])},
        )


# ------------------------------------------------------------------ templates
def _template(category: NotificationCategory, channel: str, locale: str) -> ChannelTemplate:
    for candidate in dict.fromkeys([locale, locale.split("-")[0], "en"]):
        override = NotificationTemplate.objects.filter(
            category=category.key, channel=channel, locale=candidate, is_active=True
        ).first()
        if override:
            return ChannelTemplate(override.subject, override.body)
    return category.templates[channel]


def list_templates() -> QuerySet[NotificationTemplate]:
    return NotificationTemplate.objects.order_by("category", "channel", "locale")


def get_template(template_id: UUID | str) -> NotificationTemplate | None:
    return NotificationTemplate.objects.filter(pk=template_id).first()


@transaction.atomic
def save_template(
    *,
    actor_id: UUID,
    organisation_id: UUID,
    instance: NotificationTemplate | None,
    data: dict[str, Any],
) -> NotificationTemplate:
    template = instance or NotificationTemplate(organisation_id=organisation_id)
    for field in ("category", "channel", "locale", "subject", "body", "is_active"):
        if field in data:
            setattr(template, field, data[field])
    category = registry.get(template.category)
    if template.channel not in category.templates:
        raise BusinessRuleViolation(
            _("This channel is not available for the category."),
            code="invalid_channel",
            field="channel",
        )
    try:
        rendering.validate(template.subject, category.variables)
        rendering.validate(template.body, category.variables)
    except ValueError as exc:
        raise BusinessRuleViolation(str(exc), code="invalid_template", field="body") from exc
    template.full_clean()
    template.save()
    record(
        "notification.template_saved",
        organisation_id=organisation_id,
        actor_id=actor_id,
        resource_type="notification_template",
        resource_id=template.id,
        metadata={
            "category": template.category,
            "channel": template.channel,
            "locale": template.locale,
        },
    )
    return template


# ------------------------------------------------------------------ sending
def notify(
    organisation_id: UUID,
    *,
    recipients: Iterable[User],
    category: str,
    context: dict[str, object],
    data: dict[str, Any] | None = None,
    dedupe_key: str = "",
) -> list[Notification]:
    """Notify members of the organisation in context. ``context`` holds display values for
    the template placeholders; keep it free of anything the recipient should not see."""
    definition = registry.get(category)
    organisation = get_organisation(organisation_id)
    default_locale = organisation.default_locale if organisation else "en"
    created: list[Notification] = []
    for user in recipients:
        if not user.is_active:
            continue
        channels = enabled_channels(user, definition)
        locale = user.locale or default_locale or "en"
        in_app = (
            _template(definition, "in_app", locale) if "in_app" in definition.templates else None
        )
        try:
            with transaction.atomic():
                notification = Notification.objects.create(
                    recipient=user,
                    category=definition.key,
                    title=rendering.render(in_app.subject, context)
                    if in_app
                    else definition.description,
                    body=rendering.render(in_app.body, context) if in_app else "",
                    data=data or {},
                    show_in_app="in_app" in channels,
                    dedupe_key=dedupe_key,
                )
        except IntegrityError:
            continue  # already notified for this dedupe key
        for channel in EXTERNAL_CHANNELS:
            if channel not in channels:
                continue
            destination = (
                user.email
                if channel == "email"
                else (user.phone if user.phone_verified_at else None)
            )
            if not destination:
                continue
            template = _template(definition, channel, locale)
            delivery = NotificationDelivery.objects.create(
                notification=notification,
                channel=channel,
                destination=destination,
                subject=rendering.render(template.subject, context)[:200],
                body=rendering.render(template.body, context),
            )
            _queue(delivery)
        created.append(notification)
    return created


def _queue(delivery: NotificationDelivery) -> None:
    from .tasks import deliver

    delivery_id, organisation_id = str(delivery.id), str(delivery.organisation_id)
    transaction.on_commit(
        lambda: deliver.delay(delivery_id=delivery_id, organisation_id=organisation_id), robust=True
    )


# ------------------------------------------------------------------ inbox
def inbox(user: User, *, unread_only: bool = False) -> QuerySet[Notification]:
    items = Notification.objects.filter(recipient=user, show_in_app=True)
    return items.filter(read_at__isnull=True) if unread_only else items


def unread_count(user: User) -> int:
    return inbox(user, unread_only=True).count()


def mark_read(user: User, notification_id: UUID | str) -> Notification:
    notification = inbox(user).filter(pk=notification_id).first()
    if notification is None:
        raise NotFoundError(_("Notification not found."))
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=["read_at", "updated_at"])
    return notification


def mark_all_read(user: User) -> int:
    return inbox(user, unread_only=True).update(read_at=timezone.now())


@transaction.atomic
def delete_template(
    *, actor_id: UUID, organisation_id: UUID, template: NotificationTemplate
) -> None:
    template_id = template.id
    template.delete()
    record(
        "notification.template_deleted",
        organisation_id=organisation_id,
        actor_id=actor_id,
        resource_type="notification_template",
        resource_id=template_id,
    )
