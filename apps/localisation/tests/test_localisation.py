from datetime import date
from decimal import Decimal

import pytest

from apps.localisation.public import format_money, minor_units, money, tax_rate
from common.money import CurrencyMismatch, Money

pytestmark = pytest.mark.django_db


def test_supported_countries_are_listed_with_configuration(make_user, client_for):
    client = client_for(make_user())
    codes = {c["code"] for c in client.get("/api/v1/countries/").json()}
    assert codes == {"UG", "KE", "TZ", "RW"}
    uganda = client.get("/api/v1/countries/ug/").json()
    assert uganda["default_currency"] == "UGX"
    assert uganda["configuration"]["financial_year_start_month"] == 7
    assert client.get("/api/v1/countries/ZZ/").status_code == 404


def test_tax_rates_endpoint(make_user, client_for):
    rates = client_for(make_user()).get("/api/v1/countries/KE/tax-rates/").json()
    assert {r["tax_code"]: r["rate"] for r in rates} == {
        "VAT_STANDARD": "16.0000",
        "VAT_ZERO": "0.0000",
    }


def test_tax_rate_lookup_respects_validity_dates():
    rate = tax_rate("UG", "VAT_STANDARD", on=date(2026, 1, 1))
    assert rate is not None and rate.rate == Decimal("18")
    assert tax_rate("UG", "VAT_STANDARD", on=date(2019, 1, 1)) is None


def test_reference_endpoints_need_authentication(api):
    assert api.get("/api/v1/countries/").status_code == 401


def test_money_rounds_to_currency_precision():
    assert minor_units("UGX") == 0 and minor_units("KES") == 2
    assert money("1234.5", "UGX").amount == Decimal("1235")
    assert money("10.005", "KES").amount == Decimal("10.01")


def test_money_formatting_for_documents():
    ugx = format_money(Money("1500000.4", "UGX"), locale="en")
    assert "UGX" in ugx and ugx.endswith("1,500,000")  # no decimals for UGX
    assert format_money(Money("1234.5", "KES"), locale="en").endswith("1,234.50")


@pytest.mark.parametrize("bad", [1.5, "abc", "NaN"])
def test_money_rejects_floats_and_invalid_amounts(bad):
    with pytest.raises((TypeError, ValueError)):
        Money(bad, "UGX")


def test_money_never_mixes_currencies():
    with pytest.raises(CurrencyMismatch):
        Money("1", "UGX") + Money("1", "KES")
    assert (Money("2.50", "KES") * 3).amount == Decimal("7.50")


def test_branch_in_unsupported_country_is_rejected(new_org, client_for):
    org, owner = new_org()
    response = client_for(owner, org).post(
        "/api/v1/branches/", {"name": "Lagos", "country_code": "NG"}, format="json"
    )
    assert (
        response.status_code == 400
        and response.json()["errors"][0]["code"] == "country_not_supported"
    )
