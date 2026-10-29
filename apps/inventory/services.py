"""Stock rules live here so the API, admin and future apps all use the same logic."""
from django.db import transaction
from django.db.models import F

from .models import StockBatch, StockMovement


class InsufficientStock(Exception):
    def __init__(self, product, requested, available):
        self.product, self.requested, self.available = product, requested, available
        super().__init__(f"Only {available} of {product} in stock, {requested} requested.")


@transaction.atomic
def receive_stock(*, business, branch, product, quantity, cost_price, user=None,
                  batch_number="", expiry_date=None, supplier=None):
    batch = StockBatch.objects.create(
        business=business, branch=branch, product=product, supplier=supplier,
        batch_number=batch_number, expiry_date=expiry_date, cost_price=cost_price,
        quantity_received=quantity, quantity_remaining=quantity,
    )
    StockMovement.objects.create(
        business=business, branch=branch, product=product, batch=batch,
        kind=StockMovement.Kind.RECEIVED, quantity=quantity, unit_cost=cost_price, created_by=user,
    )
    return batch


def allocate_fefo(*, product, branch, quantity, today=None):
    """Pick batches first-expiry-first-out. Expired batches are never sold.

    Returns [(batch, qty), ...]. Locks the rows; call inside a transaction.
    """
    from django.utils import timezone

    today = today or timezone.localdate()
    batches = (
        StockBatch.objects.select_for_update()
        .filter(product=product, branch=branch, quantity_remaining__gt=0)
        .exclude(expiry_date__lt=today)
        .order_by(F("expiry_date").asc(nulls_last=True), "received_at")
    )
    picks, needed = [], quantity
    for batch in batches:
        take = min(needed, batch.quantity_remaining)
        picks.append((batch, take))
        needed -= take
        if needed == 0:
            return picks
    raise InsufficientStock(product, quantity, quantity - needed)


@transaction.atomic
def write_off(*, batch, quantity, kind, reason="", user=None):
    """Remove stock for expiry, damage or a count difference (a loss)."""
    batch = StockBatch.objects.select_for_update().get(pk=batch.pk)
    if quantity > batch.quantity_remaining:
        raise InsufficientStock(batch.product, quantity, batch.quantity_remaining)
    batch.quantity_remaining -= quantity
    batch.save(update_fields=["quantity_remaining", "updated_at"])
    return StockMovement.objects.create(
        business=batch.business, branch=batch.branch, product=batch.product, batch=batch,
        kind=kind, quantity=-quantity, unit_cost=batch.cost_price, reason=reason, created_by=user,
    )
