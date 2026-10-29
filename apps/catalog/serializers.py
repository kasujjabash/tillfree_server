from rest_framework import serializers

from .models import Category, Product


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class ProductSerializer(serializers.ModelSerializer):
    stock_on_hand = serializers.IntegerField(read_only=True)
    category_name = serializers.CharField(source="category.name", read_only=True, default=None)

    class Meta:
        model = Product
        fields = [
            "id", "name", "generic_name", "brand", "category", "category_name", "sku", "barcode",
            "unit", "selling_price", "reorder_level", "description", "image", "location",
            "attributes", "requires_prescription", "is_active", "visible_in_store", "stock_on_hand",
        ]

    def validate_category(self, category):
        business = self.context.get("business")
        if category and business and category.business_id != business.id:
            raise serializers.ValidationError("Category belongs to another business.")
        return category
