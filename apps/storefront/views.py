"""Public endpoints a customer's phone calls after scanning the shop's QR code.

No login needed to browse. Never expose cost prices or exact stock counts.
"""
from django.db.models import Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404
from rest_framework import generics, serializers
from rest_framework.permissions import AllowAny
from rest_framework.throttling import AnonRateThrottle

from apps.businesses.models import Business
from apps.catalog.models import Category, Product

LOW_STOCK_SHOWN_AS = 5  # show "Only 3 left" at or below this


class StoreThrottle(AnonRateThrottle):
    rate = "120/min"


class PublicMixin:
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [StoreThrottle]

    def get_business(self):
        if getattr(self, "swagger_fake_view", False):  # API docs generation
            return None
        return get_object_or_404(Business, slug=self.kwargs["slug"], is_active=True)


class StoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = Business
        fields = ["name", "slug", "industry_pack", "phone", "address", "currency"]


class StoreCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "parent"]


class StoreProductSerializer(serializers.ModelSerializer):
    availability = serializers.SerializerMethodField()
    category_name = serializers.CharField(source="category.name", default=None)

    class Meta:
        model = Product
        fields = ["id", "name", "generic_name", "brand", "category", "category_name", "unit",
                  "selling_price", "image", "location", "description", "requires_prescription",
                  "availability"]

    def get_availability(self, obj) -> dict:
        if obj.stock <= 0:
            return {"status": "out_of_stock", "label": "Out of stock"}
        if obj.stock <= LOW_STOCK_SHOWN_AS:
            return {"status": "low", "label": f"Only {obj.stock} left"}
        return {"status": "in_stock", "label": "In stock"}


class StoreDetailView(PublicMixin, generics.RetrieveAPIView):
    serializer_class = StoreSerializer

    def get_object(self):
        return self.get_business()


class StoreCategoryList(PublicMixin, generics.ListAPIView):
    serializer_class = StoreCategorySerializer
    pagination_class = None

    def get_queryset(self):
        return Category.objects.filter(business=self.get_business())


class StoreProductList(PublicMixin, generics.ListAPIView):
    """?search=panadol  ?category=3  ?barcode=6001234567890  ?in_stock=1"""

    serializer_class = StoreProductSerializer
    search_fields = ["name", "generic_name", "brand", "barcode"]
    filterset_fields = ["category", "barcode"]

    def get_queryset(self):
        qs = (
            Product.objects.filter(business=self.get_business(), is_active=True, visible_in_store=True)
            .select_related("category")
            .annotate(stock=Coalesce(Sum("batches__quantity_remaining",
                                         filter=Q(batches__quantity_remaining__gt=0)), 0))
            .order_by("name")
        )
        if self.request.query_params.get("in_stock"):
            qs = qs.filter(stock__gt=0)
        return qs
