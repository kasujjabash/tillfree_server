import io
import json
import urllib.error
from email.message import Message

import pytest
from django.test import override_settings

from apps.notifications.sms import AfricasTalkingGateway, PermanentSmsError, TransientSmsError

SETTINGS = {
    "AFRICASTALKING_USERNAME": "sandbox",
    "AFRICASTALKING_API_KEY": "key",
    "AFRICASTALKING_SENDER_ID": "",
    "AFRICASTALKING_SANDBOX": True,
}


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _respond(monkeypatch, *, body=None, error=None):
    sent = {}

    def fake_urlopen(request, timeout):
        sent["url"], sent["headers"], sent["data"] = (
            request.full_url,
            dict(request.headers),
            request.data,
        )
        if error:
            raise error
        return FakeResponse(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return sent


@override_settings(**SETTINGS)
def test_successful_send_returns_message_id(monkeypatch):
    sent = _respond(
        monkeypatch,
        body={"SMSMessageData": {"Recipients": [{"status": "Success", "messageId": "ATX1"}]}},
    )
    assert AfricasTalkingGateway().send("+256712345678", "Hi") == "ATX1"
    assert "sandbox" in sent["url"] and sent["headers"]["Apikey"] == "key"
    assert b"to=%2B256712345678" in sent["data"]


@override_settings(**SETTINGS)
def test_rejected_number_is_permanent(monkeypatch):
    _respond(
        monkeypatch, body={"SMSMessageData": {"Recipients": [{"status": "InvalidPhoneNumber"}]}}
    )
    with pytest.raises(PermanentSmsError):
        AfricasTalkingGateway().send("+256700", "Hi")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (urllib.error.HTTPError("u", 503, "down", Message(), None), TransientSmsError),
        (urllib.error.HTTPError("u", 401, "bad key", Message(), None), PermanentSmsError),
        (urllib.error.URLError("dns"), TransientSmsError),
    ],
)
@override_settings(**SETTINGS)
def test_errors_are_classified_for_retries(monkeypatch, error, expected):
    _respond(monkeypatch, error=error)
    with pytest.raises(expected):
        AfricasTalkingGateway().send("+256712345678", "Hi")
