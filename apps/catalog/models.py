from django.db import models
from django.db.models import Q, Sum

from apps.businesses.models import TenantModel


class Category(TenantModel):
    name = models.CharField(max_length=100)
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")

    class Meta:
        verbose_name_plural = "categories"
        unique_together = [("business", "name", "parent")]
        ordering = ["name"]

    def __str__(self):
        return self.name


class Product(TenantModel):
    """Something the shop sells.

    Fields every industry needs live in columns. Pack-specific details
    (e.g. strength and dosage form for pharmacy, size for fashion) go in
    `attributes`, so new packs don't need database changes.
    """

    name = models.CharField(max_length=200)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True, related_name="products")
    brand = models.CharField(max_length=100, blank=True)
    sku = models.CharField(max_length=50, blank=True)
    barcode = models.CharField(max_length=50, blank=True, db_index=True)
    unit = models.CharField(max_length=30, default="piece", help_text="tablet, bottle, box, kg...")
    selling_price = models.DecimalField(max_digits=12, decimal_places=2)
    reorder_level = models.PositiveIntegerField(default=0, help_text="Alert when stock falls to this level.")
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to="products/", blank=True)
    location = models.CharField(max_length=50, blank=True, help_text="Shelf or aisle, shown to customers.")
    attributes = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    visible_in_store = models.BooleanField(default=True, help_text="Show to customers who scan the QR code.")

    # Pharmacy pack
    generic_name = models.CharField(max_length=200, blank=True)
    requires_prescription = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                fields=["business", "barcode"], condition=~Q(barcode=""), name="unique_barcode_per_business"
            ),
            models.UniqueConstraint(
                fields=["business", "sku"], condition=~Q(sku=""), name="unique_sku_per_business"
            ),
        ]

    def __str__(self):
        return self.name

    def stock_on_hand(self, branch=None):
        qs = self.batches.filter(quantity_remaining__gt=0)
        if branch is not None:
            qs = qs.filter(branch=branch)
        return qs.aggregate(total=Sum("quantity_remaining"))["total"] or 0
