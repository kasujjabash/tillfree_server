from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.businesses.models import Business, Membership
from apps.inventory.models import StockMovement
from apps.inventory.services import allocate_fefo

from .models import Sale, SaleItem

Role = Membership.Role
PRESCRIPTION_ROLES = {Role.OWNER, Role.MANAGER, Role.PHARMACIST}


class SaleError(Exception):
    pass


def _next_number(business):
    # Lock the business row so two tills can't take the same number.
    Business.objects.select_for_update().get(pk=business.pk)
    today = timezone.localdate()
    prefix = today.strftime("S%y%m%d-")
    count = Sale.objects.filter(business=business, number__startswith=prefix).count()
    return f"{prefix}{count + 1:04d}"


@transaction.atomic
def create_counter_sale(*, membership, branch, lines, payment_method=Sale.PaymentMethod.CASH,
                        payment_reference="", discount=Decimal("0"), customer_name="", customer_phone=""):
    """Sell at the counter. `lines` = [{"product": Product, "quantity": int}, ...].

    Takes stock first-expiry-first-out, records every batch used and the
    stock movements, and stores cost so profit is exact.
    """
    business, user = membership.business, membership.user
    if not lines:
        raise SaleError("A sale needs at least one item.")

    for line in lines:
        if line["product"].requires_prescription and membership.role not in PRESCRIPTION_ROLES:
            raise SaleError(f"{line['product']} needs a prescription; a pharmacist must make this sale.")

    sale = Sale.objects.create(
        business=business, branch=branch, number=_next_number(business),
        channel=Sale.Channel.COUNTER, status=Sale.Status.COMPLETED,
        payment_method=payment_method, payment_reference=payment_reference,
        customer_name=customer_name, customer_phone=customer_phone,
        served_by=user, completed_at=timezone.now(),
    )

    subtotal = cost_total = Decimal("0")
    for line in lines:
        product, qty = line["product"], line["quantity"]
        for batch, take in allocate_fefo(product=product, branch=branch, quantity=qty):
            batch.quantity_remaining -= take
            batch.save(update_fields=["quantity_remaining", "updated_at"])
            SaleItem.objects.create(sale=sale, product=product, batch=batch, quantity=take,
                                    unit_price=product.selling_price, unit_cost=batch.cost_price)
            StockMovement.objects.create(
                business=business, branch=branch, product=product, batch=batch,
                kind=StockMovement.Kind.SALE, quantity=-take, unit_cost=batch.cost_price,
                reference=sale.number, created_by=user,
            )
            subtotal += take * product.selling_price
            cost_total += take * batch.cost_price

    if discount < 0 or discount > subtotal:
        raise SaleError("Discount must be between 0 and the sale subtotal.")
    sale.subtotal, sale.discount, sale.cost_total = subtotal, discount, cost_total
    sale.total = subtotal - discount
    sale.save(update_fields=["subtotal", "discount", "total", "cost_total", "updated_at"])
    return sale


@transaction.atomic
def refund_sale(*, sale, user, reason=""):
    """Put every item back into the batch it came from."""
    sale = Sale.objects.select_for_update().get(pk=sale.pk)
    if sale.status != Sale.Status.COMPLETED:
        raise SaleError("Only completed sales can be refunded.")
    for item in sale.items.select_related("batch"):
        batch = item.batch
        batch.quantity_remaining += item.quantity
        batch.save(update_fields=["quantity_remaining", "updated_at"])
        StockMovement.objects.create(
            business=sale.business, branch=sale.branch, product=item.product, batch=batch,
            kind=StockMovement.Kind.SALE_RETURN, quantity=item.quantity, unit_cost=item.unit_cost,
            reference=sale.number, reason=reason, created_by=user,
        )
    sale.status = Sale.Status.REFUNDED
    sale.save(update_fields=["status", "updated_at"])
    return sale
