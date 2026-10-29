from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Business(TimeStampedModel):
    """A tenant: one shop or chain. Every business record points back here."""

    class IndustryPack(models.TextChoices):
        PHARMACY = "pharmacy", "Pharmacy"
        SUPERMARKET = "supermarket", "Supermarket"
        RESTAURANT = "restaurant", "Restaurant & Cafe"
        HARDWARE = "hardware", "Hardware & Electronics"
        FASHION = "fashion", "Fashion & Boutique"
        CLINIC = "clinic", "Clinic"

    name = models.CharField(max_length=150)
    slug = models.SlugField(unique=True, help_text="Used in the customer QR link: /store/<slug>/")
    industry_pack = models.CharField(
        max_length=20, choices=IndustryPack.choices, default=IndustryPack.PHARMACY
    )
    enabled_addons = models.JSONField(default=list, blank=True, help_text='e.g. ["loyalty", "delivery"]')
    phone = models.CharField(max_length=20, blank=True)
    address = models.CharField(max_length=255, blank=True)
    currency = models.CharField(max_length=3, default="UGX")
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "businesses"
        ordering = ["name"]

    def __str__(self):
        return self.name


class Branch(TimeStampedModel):
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="branches")
    name = models.CharField(max_length=150)
    address = models.CharField(max_length=255, blank=True)
    is_main = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "branches"
        unique_together = [("business", "name")]
        ordering = ["business", "-is_main", "name"]

    def __str__(self):
        return f"{self.business} - {self.name}"


class Membership(TimeStampedModel):
    """Links a staff user to a business with a role."""

    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        MANAGER = "manager", "Manager"
        PHARMACIST = "pharmacist", "Pharmacist"
        CASHIER = "cashier", "Cashier"
        STOCK_KEEPER = "stock_keeper", "Stock keeper"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="memberships")
    role = models.CharField(max_length=20, choices=Role.choices)
    branch = models.ForeignKey(
        Branch, on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Leave empty for access to all branches.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = [("user", "business")]

    def __str__(self):
        return f"{self.user} @ {self.business} ({self.role})"


class TenantModel(TimeStampedModel):
    """Base for every record that belongs to one business."""

    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name="+")

    class Meta:
        abstract = True
