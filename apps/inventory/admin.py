from django.contrib import admin

from .models import StockBatch, StockMovement, Supplier


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "business")
    search_fields = ("name",)


@admin.register(StockBatch)
class StockBatchAdmin(admin.ModelAdmin):
    list_display = ("product", "batch_number", "expiry_date", "quantity_remaining", "cost_price", "branch")
    list_filter = ("business", "branch")
    search_fields = ("product__name", "batch_number")


@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ("created_at", "product", "kind", "quantity", "reference", "created_by")
    list_filter = ("business", "kind")
    readonly_fields = [f.name for f in StockMovement._meta.fields]

    def has_add_permission(self, request):
        return False  # movements are created by the stock services only
