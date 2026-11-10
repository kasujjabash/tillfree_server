from django.urls import path

from . import views

urlpatterns = [
    path("plans/", views.PlanListView.as_view(), name="plan-list"),
    path("subscription/", views.SubscriptionView.as_view(), name="subscription"),
    path(
        "subscription/change-plan/", views.ChangePlanView.as_view(), name="subscription-change-plan"
    ),
    path(
        "subscription/cancellation/", views.CancelView.as_view(), name="subscription-cancellation"
    ),
    path("subscription/invoices/", views.InvoiceListView.as_view(), name="subscription-invoices"),
    path(
        "platform/organisations/<uuid:organisation_id>/entitlement-overrides/",
        views.PlatformOverrideView.as_view(),
        name="platform-entitlement-overrides",
    ),
    path(
        "platform/organisations/<uuid:organisation_id>/invoices/<uuid:invoice_id>/mark-paid/",
        views.PlatformInvoicePaidView.as_view(),
        name="platform-invoice-paid",
    ),
]
