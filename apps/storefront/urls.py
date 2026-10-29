from django.urls import path

from .views import StoreCategoryList, StoreDetailView, StoreProductList

urlpatterns = [
    path("<slug:slug>/", StoreDetailView.as_view(), name="store-detail"),
    path("<slug:slug>/categories/", StoreCategoryList.as_view(), name="store-categories"),
    path("<slug:slug>/products/", StoreProductList.as_view(), name="store-products"),
]
