from functools import cache
from zoneinfo import available_timezones

import phonenumbers
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _


def validate_e164_phone(value: str) -> None:
    """Accept only valid numbers already in E.164 form, e.g. ``+256712345678``."""
    message = _("Enter a valid phone number in international format, e.g. +256712345678.")
    try:
        parsed = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as exc:
        raise ValidationError(message, code="invalid_phone") from exc
    if not phonenumbers.is_valid_number(parsed) or (
        phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164) != value
    ):
        raise ValidationError(message, code="invalid_phone")


@cache
def _timezones() -> frozenset[str]:
    return frozenset(available_timezones())


def validate_timezone(value: str) -> None:
    if value not in _timezones():
        raise ValidationError(
            _("Enter a valid IANA timezone, e.g. Africa/Kampala."), code="invalid_timezone"
        )
