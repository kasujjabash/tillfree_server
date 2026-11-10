"""Country and currency reference data (docs/architecture/06-localisation.md).

Global reference tables: readable by everyone, changed only by platform staff (Django
admin). Rows are disabled, never deleted, so existing organisations keep working.
"""

from datetime import date
from typing import ClassVar

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel
from common.validators import validate_timezone


class Currency(models.Model):
    code = models.CharField(max_length=3, primary_key=True, help_text="ISO 4217")
    name = models.CharField(max_length=100)
    minor_units = models.PositiveSmallIntegerField(help_text="Digits after the decimal point.")
    symbol = models.CharField(max_length=10, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "localisation_currency"
        verbose_name_plural = "currencies"
        ordering: ClassVar[list[str]] = ["code"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{3}$"), name="loc_currency_code_iso"
            ),
            models.CheckConstraint(
                condition=models.Q(minor_units__lte=4), name="loc_currency_minor_units"
            ),
        ]

    def __str__(self) -> str:
        return self.code


class Country(models.Model):
    code = models.CharField(max_length=2, primary_key=True, help_text="ISO 3166-1 alpha-2")
    name = models.CharField(max_length=100)
    calling_code = models.CharField(max_length=6, help_text="E.164 country calling code, e.g. 256")
    default_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    default_timezone = models.CharField(max_length=64, validators=[validate_timezone])
    languages = ArrayField(
        models.CharField(max_length=35), default=list, help_text="BCP 47, preferred first"
    )
    is_enabled = models.BooleanField(
        default=False, help_text=_("Organisations can be registered in enabled countries only.")
    )

    class Meta:
        db_table = "localisation_country"
        verbose_name_plural = "countries"
        ordering: ClassVar[list[str]] = ["name"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(code__regex=r"^[A-Z]{2}$"), name="loc_country_code_iso"
            ),
        ]

    def __str__(self) -> str:
        return self.name


class CountryConfiguration(TimeStampedModel):
    """How a country formats and regulates things. Versioned: each change bumps ``version``."""

    country = models.OneToOneField(
        Country, on_delete=models.CASCADE, primary_key=True, related_name="configuration"
    )
    date_format = models.CharField(max_length=20, default="dd/MM/yyyy", help_text="CLDR pattern")
    decimal_separator = models.CharField(max_length=1, default=".")
    group_separator = models.CharField(max_length=1, default=",", blank=True)
    address_format = models.JSONField(
        default=dict,
        blank=True,
        help_text='e.g. {"required": ["line1", "locality"], "order": ["line1", "locality"]}',
    )
    financial_year_start_month = models.PositiveSmallIntegerField(default=1)
    payment_provider_codes = ArrayField(models.CharField(max_length=50), default=list, blank=True)
    sms_provider_code = models.CharField(max_length=50, blank=True)
    regulatory = models.JSONField(
        default=dict, blank=True, help_text="Flags such as e-invoicing requirements."
    )
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "localisation_countryconfiguration"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(financial_year_start_month__gte=1)
                & models.Q(financial_year_start_month__lte=12),
                name="loc_config_fy_month",
            ),
        ]

    def __str__(self) -> str:
        return f"Configuration for {self.country_id} (v{self.version})"


class TaxRate(UUIDPrimaryKeyModel, TimeStampedModel):
    """A tax rate valid for a date range. Once used, never edit it: end it and add a new one."""

    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="tax_rates")
    tax_code = models.CharField(max_length=30, help_text="e.g. VAT_STANDARD, VAT_ZERO")
    name = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=7, decimal_places=4, help_text="Percent, e.g. 18.0000")
    valid_from = models.DateField()
    valid_to = models.DateField(null=True, blank=True, help_text="Exclusive end date.")

    class Meta:
        db_table = "localisation_taxrate"
        ordering: ClassVar[list[str]] = ["country", "tax_code", "-valid_from"]
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(rate__gte=0) & models.Q(rate__lte=100), name="loc_taxrate_range"
            ),
            models.CheckConstraint(
                condition=models.Q(valid_to__isnull=True)
                | models.Q(valid_to__gt=models.F("valid_from")),
                name="loc_taxrate_dates",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.country_id} {self.tax_code} {self.rate}%"

    def applies_on(self, day: date) -> bool:
        return self.valid_from <= day and (self.valid_to is None or day < self.valid_to)
