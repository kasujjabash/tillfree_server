from django.conf import settings
from django.db import models

from apps.businesses.models import Branch, TenantModel
from apps.catalog.models import Product
from apps.inventory.models import StockBatch


class Sale(TenantModel):
    class Channel(models.TextChoices):
        COUNTER = "counter", "Counter (staff)"
        SELF_CHECKOUT = "self_checkout", "Self-checkout (customer phone)"

    class Status(models.TextChoices):
        PENDING_PAYMENT = "pending_payment", "Waiting for payment"
        COMPLETED = "completed", "Completed"
        REFUNDED = "refunded", "Refunded"
        CANCELLED = "cancelled", "Cancelled"

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        MTN_MOMO = "mtn_momo", "MTN Mobile Money"
        AIRTEL_MONEY = "airtel_money", "Airtel Money"
        CARD = "card", "Card"

    number = models.CharField(max_length=30)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="sales")
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.COUNTER)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.COMPLETED)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    payment_reference = models.CharField(max_length=100, blank=True)
    customer_name = models.CharField(max_length=150, blank=True)
    customer_phone = models.CharField(max_length=20, blank=True)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    cost_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    served_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = [("business", "number")]
        indexes = [models.Index(fields=["business", "status", "completed_at"])]

    def __str__(self):
        return self.number

    @property
    def gross_profit(self):
        return self.total - self.cost_total


class SaleItem(models.Model):
    """One line per batch used, so profit uses the real cost of what was sold."""

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField()
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=2)

    @property
    def line_total(self):
        return self.quantity * self.unit_price
