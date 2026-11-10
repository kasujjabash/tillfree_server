"""Reference data for the first markets: Uganda, Kenya, Tanzania, Rwanda.

Tax rates are the standard VAT rates at the time of writing and MUST be confirmed with
a tax adviser before invoices are issued. Fiscal-invoicing systems are recorded as
information for future integrations; supporting them is not implemented.
"""

from datetime import date
from decimal import Decimal

from django.db import migrations

CURRENCIES = [
    ("UGX", "Ugandan shilling", 0, "USh"),
    ("KES", "Kenyan shilling", 2, "KSh"),
    ("TZS", "Tanzanian shilling", 2, "TSh"),
    ("RWF", "Rwandan franc", 0, "FRw"),
    ("USD", "US dollar", 2, "$"),
]

ADDRESS = {
    "required": ["line1", "locality"],
    "order": ["line1", "line2", "locality", "region", "postal_code"],
}

COUNTRIES = [
    # code, name, calling code, currency, timezone, languages, payment providers, fiscal system, VAT %
    (
        "UG",
        "Uganda",
        "256",
        "UGX",
        "Africa/Kampala",
        ["en", "sw"],
        ["mtn_momo_ug", "airtel_money_ug"],
        "EFRIS",
        "18",
    ),
    (
        "KE",
        "Kenya",
        "254",
        "KES",
        "Africa/Nairobi",
        ["en", "sw"],
        ["mpesa_ke", "airtel_money_ke"],
        "eTIMS",
        "16",
    ),
    (
        "TZ",
        "Tanzania",
        "255",
        "TZS",
        "Africa/Dar_es_Salaam",
        ["sw", "en"],
        ["mpesa_tz", "airtel_money_tz", "tigo_pesa_tz"],
        "EFD/VFD",
        "18",
    ),
    (
        "RW",
        "Rwanda",
        "250",
        "RWF",
        "Africa/Kigali",
        ["rw", "en", "fr", "sw"],
        ["mtn_momo_rw", "airtel_money_rw"],
        "EBM",
        "18",
    ),
]


def seed(apps, schema_editor):
    Currency = apps.get_model("localisation", "Currency")
    Country = apps.get_model("localisation", "Country")
    CountryConfiguration = apps.get_model("localisation", "CountryConfiguration")
    TaxRate = apps.get_model("localisation", "TaxRate")
    for code, name, minor, symbol in CURRENCIES:
        Currency.objects.update_or_create(
            code=code, defaults={"name": name, "minor_units": minor, "symbol": symbol}
        )
    for code, name, calling, currency, tz, languages, providers, fiscal, vat in COUNTRIES:
        country, _ = Country.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "calling_code": calling,
                "default_currency_id": currency,
                "default_timezone": tz,
                "languages": languages,
                "is_enabled": True,
            },
        )
        CountryConfiguration.objects.update_or_create(
            country=country,
            defaults={
                "date_format": "dd/MM/yyyy",
                "address_format": ADDRESS,
                "financial_year_start_month": 7,
                "payment_provider_codes": providers,
                "sms_provider_code": "africastalking",
                "regulatory": {"fiscal_invoicing_system": fiscal, "requires_legal_review": True},
            },
        )
        for tax_code, tax_name, rate in (
            ("VAT_STANDARD", "VAT", vat),
            ("VAT_ZERO", "VAT zero-rated", "0"),
        ):
            TaxRate.objects.get_or_create(
                country=country,
                tax_code=tax_code,
                valid_from=date(2020, 1, 1),
                defaults={"name": tax_name, "rate": Decimal(rate)},
            )


class Migration(migrations.Migration):
    dependencies = [("localisation", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
