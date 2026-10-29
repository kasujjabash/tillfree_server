from django.conf import settings
from django.db import models

from apps.businesses.models import Branch, TenantModel
from apps.catalog.models import Product


class Supplier(TenantModel):
    name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    address = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class StockBatch(TenantModel):
    """One delivery of one product. Tracks batch number, expiry and cost.

    Stock on hand = sum of quantity_remaining across batches. Sales take
    from the batch that expires first (FEFO).
    """

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="batches")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="batches")
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    batch_number = models.CharField(max_length=50, blank=True)
    expiry_date = models.DateField(null=True, blank=True)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Cost per unit.")
    quantity_received = models.PositiveIntegerField()
    quantity_remaining = models.PositiveIntegerField()
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "stock batches"
        ordering = ["expiry_date", "received_at"]
        indexes = [models.Index(fields=["business", "product", "branch", "expiry_date"])]

    def __str__(self):
        return f"{self.product} #{self.batch_number or self.pk} ({self.quantity_remaining})"


class StockMovement(TenantModel):
    """Every change to stock, for the audit trail and loss reports."""

    class Kind(models.TextChoices):
        RECEIVED = "received", "Received"
        SALE = "sale", "Sale"
        SALE_RETURN = "sale_return", "Sale return"
        EXPIRED = "expired", "Expired write-off"
        DAMAGED = "damaged", "Damaged"
        COUNT_ADJUSTMENT = "count_adjustment", "Stock count adjustment"

    LOSS_KINDS = {Kind.EXPIRED, Kind.DAMAGED, Kind.COUNT_ADJUSTMENT}

    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="movements")
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="movements")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    quantity = models.IntegerField(help_text="Positive adds stock, negative removes it.")
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    reference = models.CharField(max_length=50, blank=True, help_text="e.g. sale number")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["business", "kind", "created_at"])]
