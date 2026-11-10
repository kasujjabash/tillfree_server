from django.urls import path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("notifications", views.InboxViewSet, basename="notification")
router.register("notification-templates", views.TemplateViewSet, basename="notification-template")

urlpatterns = [
    path(
        "notification-preferences/",
        views.PreferencesView.as_view(),
        name="notification-preferences",
    ),
    path(
        "notification-categories/", views.CategoryListView.as_view(), name="notification-categories"
    ),
    *router.urls,
]
