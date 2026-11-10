"""Plans, entitlements and subscriptions (docs/architecture/02-project-structure.md §2).

Global catalogue (platform-managed): Module, Plan, PlanPrice, PlanEntitlement.
Tenant-owned (RLS): Subscription, EntitlementOverride, SubscriptionInvoice.

Entitlements are enforced by the backend (``access.authorize`` and limit checks), never
just by hiding things in the user interface.
"""

from datetime import datetime
from typing import ClassVar

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel
from common.money import money_amount_field
from common.tenancy.models import TenantModel


class Module(models.Model):
    class Kind(models.TextChoices):
        SHARED = "shared", _("Shared capability")
        INDUSTRY = "industry", _("Industry module")

    code = models.CharField(max_length=50, primary_key=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    kind = models.CharField(max_length=16, choices=Kind.choices)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "subscriptions_module"
        ordering: ClassVar[list[str]] = ["code"]

    def __str__(self) -> str:
        return self.name


class Plan(UUIDPrimaryKeyModel, TimeStampedModel):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=300, blank=True)
    is_public = models.BooleanField(
        default=True, help_text=_("Shown to customers for self-service.")
    )
    is_active = models.BooleanField(default=True)
    trial_days = models.PositiveSmallIntegerField(default=30)
    # {"max_members": 10, "max_branches": 2, "storage_bytes": 1073741824}; null = unlimited
    limits = models.JSONField(default=dict, blank=True)
    modules: models.ManyToManyField[Module, PlanEntitlement] = models.ManyToManyField(
        Module, through="PlanEntitlement", related_name="plans"
    )

    class Meta:
        db_table = "subscriptions_plan"
        ordering: ClassVar[list[str]] = ["code"]

    def __str__(self) -> str:
        return self.name


class PlanEntitlement(UUIDPrimaryKeyModel):
    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="entitlements")
    module = models.ForeignKey(Module, on_delete=models.PROTECT, related_name="+")

    class Meta:
        db_table = "subscriptions_planentitlement"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["plan", "module"], name="subs_plan_module_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} includes {self.module_id}"


class Interval(models.TextChoices):
    MONTH = "month", _("Monthly")
    YEAR = "year", _("Yearly")


class PlanPrice(UUIDPrimaryKeyModel):
    """Price per currency: organisations are billed in their base currency, never converted."""

    plan = models.ForeignKey(Plan, on_delete=models.CASCADE, related_name="prices")
    currency = models.CharField(max_length=3)
    amount = money_amount_field()
    interval = models.CharField(max_length=8, choices=Interval.choices)

    class Meta:
        db_table = "subscriptions_planprice"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["plan", "currency", "interval"], name="subs_price_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(amount__gte=0), name="subs_price_non_negative"
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} {self.amount} {self.currency}/{self.interval}"


CURRENT_STATUSES = ("trialing", "active", "past_due")


class Subscription(TenantModel):
    class Status(models.TextChoices):
        TRIALING = "trialing", _("Trial")
        ACTIVE = "active", _("Active")
        PAST_DUE = "past_due", _("Payment overdue")
        CANCELLED = "cancelled", _("Cancelled")

    plan = models.ForeignKey(Plan, on_delete=models.PROTECT, related_name="+")
    status = models.CharField(max_length=16, choices=Status.choices)
    interval = models.CharField(max_length=8, choices=Interval.choices, default=Interval.MONTH)
    current_period_start = models.DateTimeField()
    current_period_end = models.DateTimeField()
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    cancel_at_period_end = models.BooleanField(default=False)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "subscriptions_subscription"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["organisation"],
                condition=models.Q(status__in=CURRENT_STATUSES),
                name="subs_one_current_per_org",
            ),
            models.CheckConstraint(
                condition=models.Q(current_period_end__gt=models.F("current_period_start")),
                name="subs_period_valid",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.plan_id} ({self.status})"

    @property
    def is_current(self) -> bool:
        return self.status in CURRENT_STATUSES


class EntitlementOverride(TenantModel):
    """Platform-granted exception to the plan: enable/disable a module, or raise limits."""

    module = models.ForeignKey(
        Module, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    enabled = models.BooleanField(null=True, blank=True, help_text=_("For module overrides."))
    limits = models.JSONField(default=dict, blank=True)
    reason = models.CharField(max_length=300)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "subscriptions_entitlementoverride"

    def __str__(self) -> str:
        return f"Override for {self.organisation_id}"

    def is_active(self, now: datetime | None = None) -> bool:
        return self.expires_at is None or (now or timezone.now()) < self.expires_at


class SubscriptionInvoice(TenantModel):
    """Platform fees owed by the organisation (billing record; payment collection is Phase 4)."""

    class Status(models.TextChoices):
        ISSUED = "issued", _("Issued")
        PAID = "paid", _("Paid")
        VOID = "void", _("Void")

    subscription = models.ForeignKey(
        Subscription, on_delete=models.PROTECT, related_name="invoices"
    )
    number = models.CharField(max_length=40, unique=True)
    period_start = models.DateTimeField()
    period_end = models.DateTimeField()
    amount = money_amount_field()
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.ISSUED)
    issued_at = models.DateTimeField(default=timezone.now)
    due_at = models.DateTimeField()
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "subscriptions_invoice"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(
                fields=["subscription", "period_start"], name="subs_invoice_one_per_period"
            ),
        ]

    def __str__(self) -> str:
        return self.number
