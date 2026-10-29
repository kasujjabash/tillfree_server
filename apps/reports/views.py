from datetime import date, timedelta

from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.tenancy import SEE_COSTS, IsBusinessMember, get_membership
from apps.inventory.models import StockBatch, StockMovement
from apps.sales.models import Sale, SaleItem

ZERO = 0
MONEY = DecimalField(max_digits=16, decimal_places=2)


def _parse(value, default):
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError(f"Bad date: {value}. Use YYYY-MM-DD.")


class DashboardView(APIView):
    """Owner dashboard numbers. ?date_from=2026-10-01&date_to=2026-10-27&branch=1 (defaults: today)."""

    permission_classes = [IsBusinessMember]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        membership = get_membership(request)
        business = membership.business
        today = timezone.localdate()
        date_from = _parse(request.query_params.get("date_from"), today)
        date_to = _parse(request.query_params.get("date_to"), today)
        branch = request.query_params.get("branch")

        sales = Sale.objects.filter(business=business, status=Sale.Status.COMPLETED,
                                    completed_at__date__gte=date_from, completed_at__date__lte=date_to)
        losses = StockMovement.objects.filter(business=business, kind__in=StockMovement.LOSS_KINDS,
                                              created_at__date__gte=date_from, created_at__date__lte=date_to)
        batches = StockBatch.objects.filter(business=business, quantity_remaining__gt=0)
        if branch:
            sales, losses, batches = sales.filter(branch=branch), losses.filter(branch=branch), batches.filter(branch=branch)

        totals = sales.aggregate(
            revenue=Coalesce(Sum("total"), ZERO, output_field=MONEY),
            cost=Coalesce(Sum("cost_total"), ZERO, output_field=MONEY),
            discounts=Coalesce(Sum("discount"), ZERO, output_field=MONEY),
            count=Count("id"),
        )
        by_method = list(sales.values("payment_method").annotate(
            total=Sum("total"), count=Count("id")).order_by("-total"))

        top_products = list(
            SaleItem.objects.filter(sale__in=sales)
            .values("product_id", "product__name")
            .annotate(units_sold=Sum("quantity"),
                      revenue=Sum(F("quantity") * F("unit_price"), output_field=MONEY),
                      profit=Sum(F("quantity") * (F("unit_price") - F("unit_cost")), output_field=MONEY))
            .order_by("-revenue")[:10]
        )

        loss_value = losses.aggregate(v=Coalesce(Sum(
            ExpressionWrapper(-F("quantity") * F("unit_cost"), output_field=MONEY)), ZERO, output_field=MONEY))["v"]
        expired_on_shelf = batches.filter(expiry_date__lt=today)
        expiring_90 = batches.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=90))

        def value(qs):
            return qs.aggregate(v=Coalesce(Sum(ExpressionWrapper(
                F("quantity_remaining") * F("cost_price"), output_field=MONEY)), ZERO, output_field=MONEY))["v"]

        data = {
            "period": {"from": date_from, "to": date_to},
            "sales": {"revenue": totals["revenue"], "count": totals["count"],
                      "discounts": totals["discounts"], "by_payment_method": by_method},
            "top_products": top_products,
            "stock": {"expired_on_shelf_batches": expired_on_shelf.count(),
                      "expiring_90_days_batches": expiring_90.count()},
        }
        if membership.role in SEE_COSTS:
            data["profit"] = {
                "gross_profit": totals["revenue"] - totals["cost"],
                "cost_of_goods_sold": totals["cost"],
                "losses_written_off": loss_value,
                "net_after_losses": totals["revenue"] - totals["cost"] - loss_value,
            }
            data["stock"].update({"stock_value_at_cost": value(batches),
                                  "expired_on_shelf_value": value(expired_on_shelf),
                                  "expiring_90_days_value": value(expiring_90)})
        else:
            for p in top_products:
                p.pop("profit", None)
        return Response(data)
