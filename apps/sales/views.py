from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.businesses.tenancy import MANAGERS, TenantViewSetMixin
from apps.inventory.services import InsufficientStock

from .models import Sale
from .serializers import CounterSaleInput, SaleSerializer
from .services import SaleError, create_counter_sale, refund_sale


class SaleViewSet(TenantViewSetMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                  mixins.CreateModelMixin, viewsets.GenericViewSet):
    """POST to make a counter sale. Any staff role can sell; refunds need a manager."""

    queryset = Sale.objects.select_related("served_by").prefetch_related("items__product", "items__batch")
    serializer_class = SaleSerializer
    filterset_fields = ["status", "channel", "payment_method", "branch"]
    search_fields = ["number", "customer_phone", "customer_name"]
    ordering_fields = ["created_at", "total"]

    def create(self, request, *args, **kwargs):
        data = CounterSaleInput(data=request.data, context=self.get_serializer_context())
        data.is_valid(raise_exception=True)
        v = data.validated_data
        try:
            sale = create_counter_sale(
                membership=self.membership, branch=v["branch"], lines=v["items"],
                payment_method=v["payment_method"], payment_reference=v["payment_reference"],
                discount=v["discount"], customer_name=v["customer_name"], customer_phone=v["customer_phone"],
            )
        except (SaleError, InsufficientStock) as exc:
            raise ValidationError(str(exc))
        return Response(self.get_serializer(sale).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    def refund(self, request, pk=None):
        if self.membership.role not in MANAGERS:
            raise PermissionDenied("Only an owner or manager can refund a sale.")
        try:
            sale = refund_sale(sale=self.get_object(), user=request.user, reason=request.data.get("reason", ""))
        except SaleError as exc:
            raise ValidationError(str(exc))
        return Response(self.get_serializer(sale).data)
