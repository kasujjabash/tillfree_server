"""Transactional email. Replaced by the notifications domain in Phase 3.

Emails are queued only after the surrounding transaction commits, so a rolled-back
operation never sends a message about something that did not happen.
"""

from typing import Any

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string

from .tasks import send_email


def frontend_url(path: str, **fragment: str) -> str:
    """Link into the web app. Secrets go in the URL fragment, which browsers never send
    to servers or put in Referer headers."""
    url = f"{settings.FRONTEND_URL.rstrip('/')}/{path.lstrip('/')}"
    if fragment:
        url += "#" + "&".join(f"{k}={v}" for k, v in fragment.items())
    return url


def queue_email(*, to: str | list[str], template: str, context: dict[str, Any]) -> None:
    """Render ``<template>.subject.txt`` and ``<template>.body.txt`` and send after commit."""
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        return
    context = {"product_name": settings.PRODUCT_NAME, **context}
    subject = render_to_string(f"{template}.subject.txt", context).strip()
    body = render_to_string(f"{template}.body.txt", context)
    # robust: if the queue is unreachable the failure is logged (and alerts), but the
    # user's already-committed request still succeeds.
    transaction.on_commit(
        lambda: send_email.delay(to=recipients, subject=subject, body=body), robust=True
    )
