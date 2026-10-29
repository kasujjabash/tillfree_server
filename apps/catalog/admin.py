from django.contrib import admin

from .models import Category, Product


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "business")
    list_filter = ("business",)
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "generic_name", "brand", "selling_price", "requires_prescription", "is_active", "business")
    list_filter = ("business", "category", "requires_prescription", "is_active")
    search_fields = ("name", "generic_name", "brand", "barcode", "sku")
