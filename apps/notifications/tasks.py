from smtplib import SMTPException, SMTPRecipientsRefused
from uuid import UUID

import structlog
from celery import Task, shared_task
from django.core.mail import EmailMessage
from django.utils import timezone

from common.tenancy.context import tenant_context

from .models import NotificationDelivery
from .sms import PermanentSmsError, TransientSmsError, gateway

logger = structlog.get_logger("tillfree.notifications")

MAX_ATTEMPTS = 6


@shared_task(bind=True, max_retries=MAX_ATTEMPTS)
def deliver(self: Task, *, delivery_id: str, organisation_id: str) -> str:
    """Send one email/SMS. Idempotent: an already-sent delivery is never sent again.

    The attempt is committed before a retry is scheduled, so attempts are counted even
    though each run is its own tenant transaction.
    """
    retry_in: int | None = None
    with tenant_context(UUID(organisation_id)):
        delivery = NotificationDelivery.objects.select_for_update().filter(pk=delivery_id).first()
        if delivery is None or delivery.status != NotificationDelivery.Status.QUEUED:
            return "skipped"
        delivery.attempts += 1
        try:
            _send(delivery)
        except (SMTPRecipientsRefused, PermanentSmsError) as exc:
            return _fail(delivery, exc)
        except (SMTPException, TransientSmsError, OSError) as exc:
            if delivery.attempts >= MAX_ATTEMPTS:
                return _fail(delivery, exc)
            delivery.last_error = type(exc).__name__
            delivery.save(update_fields=["attempts", "last_error", "updated_at"])
            retry_in = min(30 * 2 ** (delivery.attempts - 1), 3600)
        else:
            delivery.status = NotificationDelivery.Status.SENT
            delivery.sent_at = timezone.now()
            delivery.last_error = ""
            delivery.save()
            return "sent"
    raise self.retry(countdown=retry_in)


def _send(delivery: NotificationDelivery) -> None:
    if delivery.channel == "email":
        EmailMessage(subject=delivery.subject, body=delivery.body, to=[delivery.destination]).send()
        delivery.provider = "smtp"
        return
    sms = gateway()
    delivery.provider_message_id = sms.send(delivery.destination, delivery.body)
    delivery.provider = sms.name


def _fail(delivery: NotificationDelivery, exc: Exception) -> str:
    delivery.status = NotificationDelivery.Status.FAILED
    # The error class only: provider messages can echo phone numbers or content.
    delivery.last_error = type(exc).__name__
    delivery.save(update_fields=["status", "attempts", "last_error", "updated_at"])
    logger.warning(
        "notification_delivery_failed", channel=delivery.channel, error=type(exc).__name__
    )
    return "failed"
