from django.urls import URLPattern, URLResolver, path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("branches", views.BranchViewSet, basename="branch")
router.register("departments", views.DepartmentViewSet, basename="department")

urlpatterns: list[URLPattern | URLResolver] = [
    path(
        "organisations/current/",
        views.CurrentOrganisationView.as_view(),
        name="organisation-current",
    ),
    path(
        "organisations/current/settings/",
        views.OrganisationSettingsView.as_view(),
        name="organisation-settings",
    ),
    *router.urls,
]
