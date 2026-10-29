from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import BranchViewSet, CurrentBusinessView, MembershipViewSet

router = DefaultRouter()
router.register("branches", BranchViewSet)
router.register("staff", MembershipViewSet)

urlpatterns = [path("business/", CurrentBusinessView.as_view(), name="current-business")] + router.urls
