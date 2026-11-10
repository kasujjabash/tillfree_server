"""Reference data for clients: supported countries, currencies and tax rates."""

from typing import Any

from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from common.exceptions import NotFoundError

from ..models import Country, CountryConfiguration, Currency, TaxRate
from ..services import enabled_countries, get_country, tax_rates


class CurrencySerializer(serializers.ModelSerializer[Currency]):
    class Meta:
        model = Currency
        fields = ("code", "name", "minor_units", "symbol")


class CountrySerializer(serializers.ModelSerializer[Country]):
    default_currency = serializers.CharField(source="default_currency_id")

    class Meta:
        model = Country
        fields: tuple[str, ...] = (
            "code",
            "name",
            "calling_code",
            "default_currency",
            "default_timezone",
            "languages",
        )


class ConfigurationSerializer(serializers.ModelSerializer[CountryConfiguration]):
    class Meta:
        model = CountryConfiguration
        fields = (
            "date_format",
            "decimal_separator",
            "group_separator",
            "address_format",
            "financial_year_start_month",
            "payment_provider_codes",
            "version",
        )


class CountryDetailSerializer(CountrySerializer):
    configuration = ConfigurationSerializer(read_only=True)

    class Meta(CountrySerializer.Meta):
        fields = (*CountrySerializer.Meta.fields, "configuration")


class TaxRateSerializer(serializers.ModelSerializer[TaxRate]):
    rate = serializers.DecimalField(max_digits=7, decimal_places=4, coerce_to_string=True)

    class Meta:
        model = TaxRate
        fields = ("tax_code", "name", "rate", "valid_from", "valid_to")


class CountryListView(APIView):
    @extend_schema(responses={200: CountrySerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(CountrySerializer(enabled_countries(), many=True).data)


class CountryDetailView(APIView):
    @extend_schema(responses={200: CountryDetailSerializer})
    def get(self, request: Request, code: str) -> Response:
        country = get_country(code.upper())
        if country is None or not country.is_enabled:
            raise NotFoundError("Country not found.")
        return Response(CountryDetailSerializer(country).data)


class CountryTaxRateView(APIView):
    @extend_schema(responses={200: TaxRateSerializer(many=True)})
    def get(self, request: Request, code: str) -> Response:
        country = get_country(code.upper())
        if country is None or not country.is_enabled:
            raise NotFoundError("Country not found.")
        rates: Any = tax_rates(country.code, on=timezone.now().date())
        return Response(TaxRateSerializer(rates, many=True).data)


class CurrencyListView(APIView):
    @extend_schema(responses={200: CurrencySerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(CurrencySerializer(Currency.objects.filter(is_active=True), many=True).data)
