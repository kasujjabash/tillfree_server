from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import StockAlertsView, StockBatchViewSet, StockMovementViewSet, SupplierViewSet

router = DefaultRouter()
router.register("suppliers", SupplierViewSet)
router.register("stock/batches", StockBatchViewSet)
router.register("stock/movements", StockMovementViewSet)

urlpatterns = [path("stock/alerts/", StockAlertsView.as_view(), name="stock-alerts")] + router.urls
