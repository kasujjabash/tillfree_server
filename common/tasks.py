"""Background tasks that belong to no domain."""

from smtplib import SMTPException

from celery import shared_task
from django.core.mail import EmailMessage


@shared_task(
    autoretry_for=(SMTPException, OSError),
    retry_backoff=True,
    retry_backoff_max=600,
    retry_jitter=True,
    max_retries=6,
)
def send_email(*, to: list[str], subject: str, body: str) -> None:
    """Deliver one plain-text email. Retries transient SMTP/network failures."""
    EmailMessage(subject=subject, body=body, to=to).send()
