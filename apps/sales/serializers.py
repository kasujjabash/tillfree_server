from rest_framework import serializers

from apps.businesses.models import Branch
from apps.catalog.models import Product
from apps.inventory.serializers import TenantOwnedField

from .models import Sale, SaleItem


class SaleItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    batch_number = serializers.CharField(source="batch.batch_number", read_only=True)
    line_total = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = SaleItem
        fields = ["product", "product_name", "batch_number", "quantity", "unit_price", "line_total"]


class SaleSerializer(serializers.ModelSerializer):
    items = SaleItemSerializer(many=True, read_only=True)
    served_by_name = serializers.CharField(source="served_by.username", read_only=True, default=None)
    gross_profit = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)

    class Meta:
        model = Sale
        fields = ["id", "number", "branch", "channel", "status", "payment_method", "payment_reference",
                  "customer_name", "customer_phone", "subtotal", "discount", "total", "gross_profit",
                  "served_by_name", "completed_at", "items"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("can_see_costs"):
            data.pop("gross_profit", None)
        return data


class SaleLineInput(serializers.Serializer):
    product = TenantOwnedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.IntegerField(min_value=1)


class CounterSaleInput(serializers.Serializer):
    branch = TenantOwnedField(queryset=Branch.objects.all())
    items = SaleLineInput(many=True)
    payment_method = serializers.ChoiceField(choices=Sale.PaymentMethod.choices, default="cash")
    payment_reference = serializers.CharField(required=False, allow_blank=True, default="")
    discount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False, default=0)
    customer_name = serializers.CharField(required=False, allow_blank=True, default="")
    customer_phone = serializers.CharField(required=False, allow_blank=True, default="")
