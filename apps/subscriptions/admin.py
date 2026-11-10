"""The global catalogue (modules, plans, prices) is managed here by platform staff.
Subscriptions, overrides and invoices are tenant data: use the platform API."""

from django.contrib import admin

from .models import Module, Plan, PlanEntitlement, PlanPrice


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "kind", "is_active")


class EntitlementInline(admin.TabularInline):
    model = PlanEntitlement
    extra = 0


class PriceInline(admin.TabularInline):
    model = PlanPrice
    extra = 0


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_public", "is_active", "trial_days")
    inlines = (EntitlementInline, PriceInline)
