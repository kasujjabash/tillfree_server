from django.urls import path

from . import views

urlpatterns = [
    path("countries/", views.CountryListView.as_view(), name="country-list"),
    path("countries/<str:code>/", views.CountryDetailView.as_view(), name="country-detail"),
    path(
        "countries/<str:code>/tax-rates/",
        views.CountryTaxRateView.as_view(),
        name="country-tax-rates",
    ),
    path("currencies/", views.CurrencyListView.as_view(), name="currency-list"),
]
