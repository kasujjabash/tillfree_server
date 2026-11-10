"""Reference data is global (not tenant-owned), so platform staff manage it here."""

from typing import Any

from django.contrib import admin
from django.http import HttpRequest

from .models import Country, CountryConfiguration, Currency, TaxRate


class ReferenceAdmin(admin.ModelAdmin):
    def has_delete_permission(self, request: HttpRequest, obj: Any = None) -> bool:
        return False  # disable instead: organisations may depend on the row

    def save_model(self, request: HttpRequest, obj: Any, form: Any, change: bool) -> None:
        if isinstance(obj, CountryConfiguration) and change:
            obj.version += 1
        super().save_model(request, obj, form, change)  # lookups are cached for 5 minutes


@admin.register(Currency)
class CurrencyAdmin(ReferenceAdmin):
    list_display = ("code", "name", "minor_units", "is_active")
    search_fields = ("code", "name")


class ConfigurationInline(admin.StackedInline):
    model = CountryConfiguration


@admin.register(Country)
class CountryAdmin(ReferenceAdmin):
    list_display = ("code", "name", "default_currency", "default_timezone", "is_enabled")
    list_filter = ("is_enabled",)
    search_fields = ("code", "name")
    inlines = (ConfigurationInline,)

    def save_formset(self, request: HttpRequest, form: Any, formset: Any, change: bool) -> None:
        for config in formset.save(commit=False):
            if (
                config.pk
                and formset.model is CountryConfiguration
                and config in [f.instance for f in formset.forms if f.has_changed()]
            ):
                config.version += 1
            config.save()
        formset.save_m2m()


@admin.register(TaxRate)
class TaxRateAdmin(ReferenceAdmin):
    list_display = ("country", "tax_code", "rate", "valid_from", "valid_to")
    list_filter = ("country", "tax_code")
