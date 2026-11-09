from django.urls import URLPattern, URLResolver, path
from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("memberships", views.MembershipViewSet, basename="membership")
router.register("roles", views.RoleViewSet, basename="role")
router.register("role-assignments", views.RoleAssignmentViewSet, basename="role-assignment")
router.register("invitations", views.InvitationViewSet, basename="invitation")
router.register("support-grants", views.SupportGrantViewSet, basename="support-grant")

urlpatterns: list[URLPattern | URLResolver] = [
    path("organisations/", views.OrganisationCreateView.as_view(), name="organisation-create"),
    path("me/memberships/", views.MyMembershipsView.as_view(), name="me-memberships"),
    path("me/access/", views.MyAccessView.as_view(), name="me-access"),
    path("permissions/", views.PermissionListView.as_view(), name="permission-list"),
    path("role-templates/", views.RoleTemplateListView.as_view(), name="role-template-list"),
    # Explicit invitation routes before the router's detail route.
    path("invitations/preview/", views.InvitationPreviewView.as_view(), name="invitation-preview"),
    path("invitations/accept/", views.InvitationAcceptView.as_view(), name="invitation-accept"),
    path(
        "invitations/accept-new/",
        views.InvitationAcceptNewView.as_view(),
        name="invitation-accept-new",
    ),
    path(
        "platform/organisations/",
        views.PlatformOrganisationListView.as_view(),
        name="platform-organisations",
    ),
    path(
        "platform/support-grants/",
        views.PlatformSupportGrantView.as_view(),
        name="platform-support-grants",
    ),
    *router.urls,
]
