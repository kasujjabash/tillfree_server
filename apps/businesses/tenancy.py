"""How each API request knows which business it is working on.

Staff send the header  X-Business: <slug>.  If they belong to exactly one
business the header can be left out. Every tenant viewset filters its
queryset to that business, so one shop can never see another shop's data.
"""
from rest_framework import permissions
from rest_framework.exceptions import NotFound, PermissionDenied

from .models import Membership

Role = Membership.Role
MANAGERS = {Role.OWNER, Role.MANAGER}
SEE_COSTS = {Role.OWNER, Role.MANAGER}  # cost prices and profit


def get_membership(request):
    if hasattr(request, "_membership"):
        return request._membership
    user = request.user
    if not user.is_authenticated:
        raise PermissionDenied("Log in first.")
    qs = Membership.objects.select_related("business").filter(
        user=user, is_active=True, business__is_active=True
    )
    slug = request.headers.get("X-Business")
    if slug:
        membership = qs.filter(business__slug=slug).first()
        if membership is None:
            raise NotFound("Business not found or you are not a member.")
    else:
        memberships = list(qs[:2])
        if len(memberships) != 1:
            raise PermissionDenied("Send the X-Business header to choose a business.")
        membership = memberships[0]
    request._membership = membership
    return membership


class IsBusinessMember(permissions.BasePermission):
    """Allows the request if the user belongs to the business.

    A view can set `write_roles` to limit create/update/delete to some roles.
    """

    def has_permission(self, request, view):
        membership = get_membership(request)
        write_roles = getattr(view, "write_roles", None)
        if request.method in permissions.SAFE_METHODS or write_roles is None:
            return True
        return membership.role in write_roles


class TenantViewSetMixin:
    """Scopes querysets to the current business and stamps it on new records."""

    permission_classes = [IsBusinessMember]

    @property
    def membership(self):
        return get_membership(self.request)

    @property
    def business(self):
        return self.membership.business

    def get_queryset(self):
        return super().get_queryset().filter(business=self.business)

    def perform_create(self, serializer):
        serializer.save(business=self.business)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        if self.request.user.is_authenticated:
            ctx["can_see_costs"] = self.membership.role in SEE_COSTS
            ctx["business"] = self.business
        return ctx
