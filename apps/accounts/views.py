from rest_framework import generics

from .serializers import MeSerializer


class MeView(generics.RetrieveUpdateAPIView):
    """The logged-in staff user and the businesses they belong to."""

    serializer_class = MeSerializer

    def get_object(self):
        return self.request.user
