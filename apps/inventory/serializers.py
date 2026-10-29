from rest_framework import serializers

from apps.businesses.models import Branch
from apps.catalog.models import Product

from .models import StockBatch, StockMovement, Supplier


class TenantOwnedField(serializers.PrimaryKeyRelatedField):
    """A related-object field limited to the current business."""

    def get_queryset(self):
        business = self.context.get("business")
        return super().get_queryset().filter(business=business)


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ["id", "name", "phone", "email", "address"]


class StockBatchSerializer(serializers.ModelSerializer):
    """POST here to receive a delivery. Quantities change only through stock movements."""

    branch = TenantOwnedField(queryset=Branch.objects.all())
    product = TenantOwnedField(queryset=Product.objects.all())
    supplier = TenantOwnedField(queryset=Supplier.objects.all(), required=False, allow_null=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    quantity = serializers.IntegerField(write_only=True, min_value=1)

    class Meta:
        model = StockBatch
        fields = [
            "id", "branch", "product", "product_name", "supplier", "batch_number", "expiry_date",
            "cost_price", "quantity", "quantity_received", "quantity_remaining", "received_at",
        ]
        read_only_fields = ["quantity_received", "quantity_remaining", "received_at"]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("can_see_costs"):
            data.pop("cost_price", None)
        return data


class WriteOffSerializer(serializers.Serializer):
    quantity = serializers.IntegerField(min_value=1)
    kind = serializers.ChoiceField(choices=sorted(StockMovement.LOSS_KINDS))
    reason = serializers.CharField(required=False, allow_blank=True)


class StockMovementSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    created_by_name = serializers.CharField(source="created_by.username", read_only=True, default=None)

    class Meta:
        model = StockMovement
        fields = ["id", "created_at", "branch", "product", "product_name", "batch", "kind",
                  "quantity", "reason", "reference", "created_by_name"]
