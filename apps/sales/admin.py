from django.contrib import admin

from .models import Sale, SaleItem


class SaleItemInline(admin.TabularInline):
    model = SaleItem
    extra = 0
    readonly_fields = ["product", "batch", "quantity", "unit_price", "unit_cost"]
    can_delete = False


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ("number", "business", "branch", "status", "payment_method", "total", "served_by", "completed_at")
    list_filter = ("business", "status", "channel", "payment_method")
    search_fields = ("number", "customer_phone")
    inlines = [SaleItemInline]
    readonly_fields = ["subtotal", "discount", "total", "cost_total"]
