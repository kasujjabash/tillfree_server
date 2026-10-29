from rest_framework import generics, viewsets

from .models import Branch, Membership
from .serializers import BranchSerializer, BusinessSerializer, MembershipSerializer
from .tenancy import MANAGERS, IsBusinessMember, TenantViewSetMixin, get_membership


class CurrentBusinessView(generics.RetrieveUpdateAPIView):
    """The business selected by the X-Business header."""

    serializer_class = BusinessSerializer
    permission_classes = [IsBusinessMember]
    write_roles = MANAGERS

    def get_object(self):
        return get_membership(self.request).business


class BranchViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    queryset = Branch.objects.all()
    serializer_class = BranchSerializer
    write_roles = MANAGERS


class MembershipViewSet(TenantViewSetMixin, viewsets.ModelViewSet):
    queryset = Membership.objects.select_related("user")
    serializer_class = MembershipSerializer
    write_roles = MANAGERS
