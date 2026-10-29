from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from rest_framework import viewsets

from apps.businesses.tenancy import MANAGERS, TenantViewSetMixin, Role

from .models import Category, Product
from .serializers import CategorySerializer, ProductSerializer

CATALOG_EDITORS = MANAGERS | {Role.PHARMACIST, Role.STOCK_KEEPER}


class CategoryViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    write_roles = CATALOG_EDITORS
    search_fields = ["name"]


class ProductViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    """Products with live stock. Search: ?search=panadol  Filter: ?category=3&barcode=..."""

    queryset = Product.objects.select_related("category")
    serializer_class = ProductSerializer
    write_roles = CATALOG_EDITORS
    search_fields = ["name", "generic_name", "brand", "barcode", "sku"]
    filterset_fields = ["category", "barcode", "requires_prescription", "is_active"]
    ordering_fields = ["name", "selling_price", "stock_on_hand"]

    def get_queryset(self):
        return super().get_queryset().annotate(
            stock_on_hand=Coalesce(
                Sum("batches__quantity_remaining", filter=Q(batches__quantity_remaining__gt=0)), 0
            )
        ).order_by("name")
