"""SMS gateways behind one small interface. Select with ``SMS_BACKEND`` (dotted path)."""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Protocol

import structlog
from django.conf import settings
from django.utils.module_loading import import_string

logger = structlog.get_logger("tillfree.sms")


class SmsError(Exception):
    pass


class TransientSmsError(SmsError):
    """Worth retrying (network, provider outage)."""


class PermanentSmsError(SmsError):
    """Will not succeed on retry (bad number, rejected message, bad credentials)."""


class SmsGateway(Protocol):
    name: str

    def send(self, to: str, message: str) -> str:
        """Send one message to an E.164 number; return the provider's message id."""
        ...


class ConsoleSmsGateway:
    """Development and tests: records messages instead of sending them."""

    name = "console"
    outbox: list[tuple[str, str]] = []  # noqa: RUF012 - shared test inbox

    def send(self, to: str, message: str) -> str:
        self.outbox.append((to, message))
        logger.info("sms_console", to_suffix=to[-4:], length=len(message))
        return f"console-{len(self.outbox)}"


class AfricasTalkingGateway:
    """Africa's Talking bulk SMS API (East and West Africa)."""

    name = "africastalking"
    LIVE_URL = "https://api.africastalking.com/version1/messaging"
    SANDBOX_URL = "https://api.sandbox.africastalking.com/version1/messaging"

    def send(self, to: str, message: str) -> str:
        fields = {"username": settings.AFRICASTALKING_USERNAME, "to": to, "message": message}
        if settings.AFRICASTALKING_SENDER_ID:
            fields["from"] = settings.AFRICASTALKING_SENDER_ID
        request = urllib.request.Request(  # noqa: S310 - fixed https URL
            self.SANDBOX_URL if settings.AFRICASTALKING_SANDBOX else self.LIVE_URL,
            data=urllib.parse.urlencode(fields).encode(),
            headers={
                "apiKey": settings.AFRICASTALKING_API_KEY,
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
                body = json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            if exc.code >= 500 or exc.code == 429:
                raise TransientSmsError(f"provider returned {exc.code}") from exc
            raise PermanentSmsError(f"provider rejected the request ({exc.code})") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TransientSmsError("provider unreachable") from exc

        recipients = body.get("SMSMessageData", {}).get("Recipients", [])
        if not recipients:
            raise PermanentSmsError(
                str(body.get("SMSMessageData", {}).get("Message", "no recipients"))
            )
        first = recipients[0]
        if first.get("status") != "Success":
            raise PermanentSmsError(f"provider status {first.get('status')}")
        return str(first.get("messageId", ""))


def gateway() -> SmsGateway:
    gateway_class = import_string(settings.SMS_BACKEND)
    instance: SmsGateway = gateway_class()
    return instance
