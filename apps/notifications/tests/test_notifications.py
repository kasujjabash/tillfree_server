import contextlib
import re
from smtplib import SMTPServerDisconnected
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from django.core import mail
from django.utils import timezone

from apps.events.models import OutboxEvent, ProcessedEvent
from apps.events.services import publish
from apps.events.tasks import relay_outbox, run_handler
from apps.notifications import rendering
from apps.notifications.models import Notification, NotificationDelivery
from apps.notifications.services import notify
from apps.notifications.sms import ConsoleSmsGateway
from apps.notifications.tasks import MAX_ATTEMPTS, deliver
from common.tenancy.context import tenant_context
from conftest import PASSWORD

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _clear_sms():
    ConsoleSmsGateway.outbox.clear()


def _token(message) -> str:
    match = re.search(r"#token=([\w\-.]+)", str(message.body))
    assert match is not None
    return match.group(1)


def test_member_joining_notifies_managers_in_app_and_by_email(
    new_org, client_for, api, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    with django_capture_on_commit_callbacks(execute=True):
        client_for(owner, org).post(
            "/api/v1/invitations/",
            {"email": "nurse@example.com", "template_key": "staff"},
            format="json",
        )
    token = _token(mail.outbox[-1])
    mail.outbox.clear()
    with django_capture_on_commit_callbacks(execute=True):
        joined = api.post(
            "/api/v1/invitations/accept-new/",
            {"token": token, "full_name": "Nurse Joy", "password": PASSWORD},
            format="json",
        )
    assert joined.status_code == 201

    inbox = client_for(owner, org).get("/api/v1/notifications/").json()["results"]
    assert [n["title"] for n in inbox] == ["New member"]
    assert "Nurse Joy" in inbox[0]["body"]
    assert any(owner.email in m.to and "Nurse Joy joined" in m.subject for m in mail.outbox)
    with tenant_context(org.id):
        assert NotificationDelivery.objects.get(channel="email").status == "sent"


def test_event_handlers_run_once_even_if_relayed_twice(new_org):
    org, _owner = new_org()
    with tenant_context(org.id):
        event = publish(
            "access.member_joined", organisation_id=org.id, payload={"membership_id": str(uuid4())}
        )
    relay_outbox()
    name = "apps.access.handlers.notify_member_managers"
    assert (
        run_handler(event_id=str(event.id), handler=name, organisation_id=str(org.id))
        == "already_processed"
    )
    assert ProcessedEvent.objects.filter(event_id=event.id).count() == 1
    assert OutboxEvent.objects.get(pk=event.id).published_at is not None


@pytest.mark.parametrize("payload", [{"nested": {"a": 1}}, {"text": "x" * 500}])
def test_event_payloads_carry_identifiers_only(payload):
    with pytest.raises((TypeError, ValueError)):
        publish("access.member_joined", organisation_id=None, payload=payload)


def test_preferences_can_turn_off_email_but_not_mandatory_notices(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    prefs = client.get("/api/v1/notification-preferences/").json()
    assert {"category": "access.member_joined", "channel": "email"}.items() <= next(
        p for p in prefs if p["category"] == "access.member_joined" and p["channel"] == "email"
    ).items()
    ok = client.put(
        "/api/v1/notification-preferences/",
        [{"category": "access.member_joined", "channel": "email", "enabled": False}],
        format="json",
    )
    assert ok.status_code == 200
    mandatory = client.put(
        "/api/v1/notification-preferences/",
        [{"category": "access.sensitive_role_granted", "channel": "email", "enabled": False}],
        format="json",
    )
    assert mandatory.status_code == 400 and mandatory.json()["code"] == "mandatory_notification"

    with tenant_context(org.id):
        notify(
            org.id,
            recipients=[owner],
            category="access.member_joined",
            context={"member_name": "A"},
        )
        assert not NotificationDelivery.objects.filter(channel="email").exists()
        assert Notification.objects.count() == 1


def test_sms_only_to_verified_phones_and_never_for_sensitive_categories(
    new_org, make_user, add_member
):
    org, _owner = new_org()
    user, _ = add_member(org, "s@example.com")
    user.phone = "+256712345678"
    user.save()
    with tenant_context(org.id):
        from apps.notifications.models import NotificationPreference

        NotificationPreference.objects.create(
            user=user, category="access.member_joined", channel="sms", enabled=True
        )
        notify(org.id, recipients=[user], category="access.member_joined", context={})
        assert not NotificationDelivery.objects.filter(channel="sms").exists()  # phone not verified
        user.phone_verified_at = timezone.now()
        user.save()
        notify(org.id, recipients=[user], category="access.member_joined", context={})
        assert NotificationDelivery.objects.filter(channel="sms").count() == 1
        notify(org.id, recipients=[user], category="access.sensitive_role_granted", context={})
        assert NotificationDelivery.objects.filter(channel="sms").count() == 1  # sensitive: no SMS


def test_delivery_retries_then_fails(new_org, monkeypatch):
    org, owner = new_org()
    with tenant_context(org.id):
        notify(org.id, recipients=[owner], category="access.member_joined", context={})
        delivery = NotificationDelivery.objects.get(channel="email")

    def smtp_down(self, *args, **kwargs):
        raise SMTPServerDisconnected("down")

    monkeypatch.setattr("django.core.mail.EmailMessage.send", smtp_down)
    for _ in range(MAX_ATTEMPTS):
        with contextlib.suppress(Retry):  # each transient failure schedules a retry
            deliver.run(delivery_id=str(delivery.id), organisation_id=str(org.id))
    with tenant_context(org.id):
        delivery.refresh_from_db()
    assert (delivery.status, delivery.attempts, delivery.last_error) == (
        "failed",
        MAX_ATTEMPTS,
        "SMTPServerDisconnected",
    )


def test_inbox_is_private_to_each_member(new_org, add_member, client_for):
    org, owner = new_org()
    other, _ = add_member(org, "o@example.com")
    with tenant_context(org.id):
        [note] = notify(org.id, recipients=[owner], category="access.member_joined", context={})
    assert client_for(other, org).post(f"/api/v1/notifications/{note.id}/read/").status_code == 404
    client = client_for(owner, org)
    assert client.get("/api/v1/notifications/unread-count/").json() == {"unread": 1}
    assert client.post(f"/api/v1/notifications/{note.id}/read/").status_code == 200
    assert client.get("/api/v1/notifications/unread-count/").json() == {"unread": 0}


def test_dedupe_key_prevents_duplicates(new_org):
    org, owner = new_org()
    with tenant_context(org.id):
        notify(
            org.id,
            recipients=[owner],
            category="access.member_joined",
            context={},
            dedupe_key="event:1",
        )
        notify(
            org.id,
            recipients=[owner],
            category="access.member_joined",
            context={},
            dedupe_key="event:1",
        )
        assert Notification.objects.count() == 1


def test_organisation_templates_override_wording_safely(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    bad = client.post(
        "/api/v1/notification-templates/",
        {
            "category": "access.member_joined",
            "channel": "in_app",
            "subject": "Hi",
            "body": "{% debug %}",
        },
        format="json",
    )
    assert bad.status_code == 400 and bad.json()["errors"][0]["code"] == "invalid_template"
    unknown = client.post(
        "/api/v1/notification-templates/",
        {
            "category": "access.member_joined",
            "channel": "in_app",
            "subject": "Hi",
            "body": "{{ password }}",
        },
        format="json",
    )
    assert unknown.status_code == 400
    ok = client.post(
        "/api/v1/notification-templates/",
        {
            "category": "access.member_joined",
            "channel": "in_app",
            "subject": "Karibu!",
            "body": "Welcome {{ member_name }}",
        },
        format="json",
    )
    assert ok.status_code == 201
    with tenant_context(org.id):
        [note] = notify(
            org.id,
            recipients=[owner],
            category="access.member_joined",
            context={"member_name": "Joy"},
        )
    assert (note.title, note.body) == ("Karibu!", "Welcome Joy")


def test_rendering_substitutes_placeholders_only():
    assert rendering.render("{{ a }} {{a}} {{ missing }}", {"a": "<b>"}) == "<b> <b> "
    with pytest.raises(ValueError, match="Only"):
        rendering.validate("{{ user.password }}", ["user"])
