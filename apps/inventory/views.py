from datetime import timedelta

from django.db.models import F, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.businesses.tenancy import MANAGERS, IsBusinessMember, Role, TenantViewSetMixin, get_membership
from apps.catalog.models import Product

from .models import StockBatch, StockMovement, Supplier
from .serializers import StockBatchSerializer, StockMovementSerializer, SupplierSerializer, WriteOffSerializer
from .services import InsufficientStock, receive_stock, write_off

STOCK_ROLES = MANAGERS | {Role.PHARMACIST, Role.STOCK_KEEPER}


class SupplierViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    write_roles = STOCK_ROLES
    search_fields = ["name", "phone"]


class StockBatchViewSet(TenantViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                        mixins.CreateModelMixin, viewsets.GenericViewSet):
    """List batches, receive a delivery (POST), or write stock off (POST /{id}/write-off/)."""

    queryset = StockBatch.objects.select_related("product")
    serializer_class = StockBatchSerializer
    write_roles = STOCK_ROLES
    filterset_fields = ["product", "branch", "supplier"]
    search_fields = ["product__name", "batch_number"]
    ordering_fields = ["expiry_date", "received_at"]

    def perform_create(self, serializer):
        data = serializer.validated_data
        serializer.instance = receive_stock(
            business=self.business, branch=data["branch"], product=data["product"],
            quantity=data["quantity"], cost_price=data["cost_price"], user=self.request.user,
            batch_number=data.get("batch_number", ""), expiry_date=data.get("expiry_date"),
            supplier=data.get("supplier"),
        )

    @action(detail=True, methods=["post"], url_path="write-off")
    def write_off(self, request, pk=None):
        serializer = WriteOffSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            movement = write_off(batch=self.get_object(), user=request.user, **serializer.validated_data)
        except InsufficientStock as exc:
            raise ValidationError(str(exc))
        return Response(StockMovementSerializer(movement).data, status=status.HTTP_201_CREATED)


class StockMovementViewSet(TenantViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Audit trail of every stock change."""

    queryset = StockMovement.objects.select_related("product", "created_by")
    serializer_class = StockMovementSerializer
    filterset_fields = ["product", "branch", "kind"]


class StockAlertsView(APIView):
    """Low stock, out of stock and expiring batches. ?days=90 sets the expiry window."""

    permission_classes = [IsBusinessMember]

    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        business = get_membership(request).business
        today = timezone.localdate()
        days = int(request.query_params.get("days", 90))

        products = (
            Product.objects.filter(business=business, is_active=True)
            .annotate(stock=Coalesce(Sum("batches__quantity_remaining",
                                         filter=Q(batches__quantity_remaining__gt=0)), 0))
        )
        low = products.filter(stock__gt=0, stock__lte=F("reorder_level"))
        out = products.filter(stock=0)

        batches = StockBatch.objects.filter(business=business, quantity_remaining__gt=0).select_related("product")
        expired = batches.filter(expiry_date__lt=today)
        expiring = batches.filter(expiry_date__gte=today, expiry_date__lte=today + timedelta(days=days))

        def p(qs):
            return [{"id": x.id, "name": x.name, "stock": x.stock, "reorder_level": x.reorder_level} for x in qs]

        def b(qs):
            return [{"batch": x.id, "product": x.product.name, "batch_number": x.batch_number,
                     "expiry_date": x.expiry_date, "quantity": x.quantity_remaining} for x in qs]

        return Response({
            "low_stock": p(low), "out_of_stock": p(out),
            "expired_on_shelf": b(expired), "expiring_soon": b(expiring), "expiry_window_days": days,
        })

