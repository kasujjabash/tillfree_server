"""Read-side access to reference data, validation of codes, tax lookup and formatting.

Reference data changes rarely, so lookups are cached (process-local, short TTL via the
cache framework) and safe to call on every request.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from babel.dates import format_date as babel_format_date
from babel.numbers import format_currency as babel_format_currency
from django.core.cache import cache
from django.utils.translation import gettext_lazy as _

from common.exceptions import BusinessRuleViolation
from common.money import Money

from .models import Country, CountryConfiguration, Currency, TaxRate

CACHE_SECONDS = 300


def _cached(key: str, loader: Any) -> Any:
    value = cache.get(key)
    if value is None:
        value = loader()
        cache.set(key, value, CACHE_SECONDS)
    return value


def minor_units(currency_code: str) -> int:
    def load() -> int:
        currency = Currency.objects.filter(code=currency_code).first()
        if currency is None:
            raise BusinessRuleViolation(_("Unknown currency."), code="unknown_currency")
        return currency.minor_units

    return int(_cached(f"loc:minor:{currency_code}", load))


def money(amount: Decimal | int | str, currency_code: str) -> Money:
    """A Money rounded to the currency's precision."""
    return Money(amount, currency_code).round_to(minor_units(currency_code))


def enabled_countries() -> list[Country]:
    return list(Country.objects.filter(is_enabled=True).select_related("default_currency"))


def get_country(code: str) -> Country | None:
    return (
        Country.objects.select_related("default_currency", "configuration")
        .filter(code=code)
        .first()
    )


def ensure_country_enabled(code: str, *, field: str = "country_code") -> Country:
    country = Country.objects.filter(code=code, is_enabled=True).first()
    if country is None:
        raise BusinessRuleViolation(
            _("Tillfree is not yet available in this country."),
            code="country_not_supported",
            field=field,
        )
    return country


def ensure_currency_active(code: str, *, field: str = "base_currency") -> Currency:
    currency = Currency.objects.filter(code=code, is_active=True).first()
    if currency is None:
        raise BusinessRuleViolation(
            _("This currency is not supported."), code="currency_not_supported", field=field
        )
    return currency


def configuration(country_code: str) -> CountryConfiguration | None:
    return CountryConfiguration.objects.filter(country_id=country_code).first()


def tax_rates(country_code: str, *, on: date) -> list[TaxRate]:
    """Rates valid on ``on`` (e.g. the transaction's business date in the branch timezone)."""
    return [r for r in TaxRate.objects.filter(country_id=country_code) if r.applies_on(on)]


def tax_rate(country_code: str, tax_code: str, *, on: date) -> TaxRate | None:
    for rate in tax_rates(country_code, on=on):
        if rate.tax_code == tax_code:
            return rate
    return None


# ------------------------------------------------------------------ formatting
# The API returns raw values; formatting is for documents, emails and SMS only.
def _babel_locale(locale: str) -> str:
    return (locale or "en").replace("-", "_")


def format_money(value: Money, *, locale: str = "en") -> str:
    return str(
        babel_format_currency(
            value.round_to(minor_units(value.currency)).amount,
            value.currency,
            locale=_babel_locale(locale),
            currency_digits=True,
        )
    )


def format_date(value: date, *, country_code: str | None = None, locale: str = "en") -> str:
    pattern = None
    if country_code:
        config = configuration(country_code)
        pattern = config.date_format if config else None
    return str(babel_format_date(value, format=pattern or "medium", locale=_babel_locale(locale)))
