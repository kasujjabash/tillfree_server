"""The member's inbox and preferences, and the organisation's notification wording."""

from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from apps.access.public import TenantAPIMixin, TenantAPIView
from apps.identity.public import User
from common.api.pagination import SmallListPagination
from common.exceptions import NotFoundError
from common.notifications import CHANNELS

from .. import registry, services
from ..models import Notification, NotificationTemplate

UUID_LOOKUP = r"[0-9a-f-]{36}"


def _user(request: Request) -> User:
    if not isinstance(request.user, User):
        raise NotAuthenticated()
    return request.user


class NotificationSerializer(serializers.ModelSerializer[Notification]):
    class Meta:
        model = Notification
        fields = ("id", "category", "title", "body", "data", "read_at", "created_at")


class PreferenceSerializer(serializers.Serializer[Any]):
    category = serializers.CharField(max_length=100)
    description = serializers.CharField(read_only=True)
    channel = serializers.ChoiceField(choices=CHANNELS)
    enabled = serializers.BooleanField()
    mandatory = serializers.BooleanField(read_only=True)


class CategorySerializer(serializers.Serializer[Any]):
    key = serializers.CharField()
    description = serializers.CharField()
    channels = serializers.ListField(child=serializers.CharField())
    variables = serializers.ListField(child=serializers.CharField())


class TemplateSerializer(serializers.ModelSerializer[NotificationTemplate]):
    class Meta:
        model = NotificationTemplate
        fields = (
            "id",
            "category",
            "channel",
            "locale",
            "subject",
            "body",
            "is_active",
            "updated_at",
        )
        read_only_fields = ("id", "updated_at")
        validators: ClassVar[list[Any]] = []


class InboxViewSet(TenantAPIMixin, mixins.ListModelMixin, viewsets.GenericViewSet[Notification]):
    """Your notifications in this organisation, newest first."""

    queryset = Notification.objects.none()  # schema introspection; get_queryset() is used
    serializer_class = NotificationSerializer
    lookup_value_regex = UUID_LOOKUP
    filter_backends = ()
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": None,
        "read": None,
        "read_all": None,
        "unread_count": None,
    }

    def get_queryset(self) -> QuerySet[Notification]:
        unread = self.request.query_params.get("unread") in ("1", "true")
        return services.inbox(_user(self.request), unread_only=unread)

    @extend_schema(parameters=[OpenApiParameter("unread", bool, required=False)])
    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        return super().list(request, *args, **kwargs)

    @extend_schema(request=None, responses={200: NotificationSerializer})
    @action(detail=True, methods=["post"])
    def read(self, request: Request, pk: str) -> Response:
        return Response(NotificationSerializer(services.mark_read(_user(request), pk)).data)

    @extend_schema(request=None, responses={200: None})
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request: Request) -> Response:
        return Response({"updated": services.mark_all_read(_user(request))})

    @extend_schema(responses={200: None})
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request: Request) -> Response:
        return Response({"unread": services.unread_count(_user(request))})


class PreferencesView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"get": None, "put": None}

    @extend_schema(responses={200: PreferenceSerializer(many=True)})
    def get(self, request: Request) -> Response:
        return Response(
            PreferenceSerializer(services.preferences_for(_user(request)), many=True).data
        )

    @extend_schema(
        request=PreferenceSerializer(many=True), responses={200: PreferenceSerializer(many=True)}
    )
    def put(self, request: Request) -> Response:
        data = PreferenceSerializer(data=request.data, many=True)
        data.is_valid(raise_exception=True)
        services.set_preferences(_user(request), data.validated_data)
        return Response(
            PreferenceSerializer(services.preferences_for(_user(request)), many=True).data
        )


class CategoryListView(TenantAPIView):
    required_permissions: ClassVar[dict[str, Any]] = {"get": None}

    @extend_schema(responses={200: CategorySerializer(many=True)})
    def get(self, request: Request) -> Response:
        items = [
            {
                "key": c.key,
                "description": c.description,
                "channels": sorted(c.templates),
                "variables": list(c.variables),
            }
            for c in registry.all_categories()
        ]
        return Response(CategorySerializer(items, many=True).data)


class TemplateViewSet(
    TenantAPIMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet[NotificationTemplate],
):
    """Your organisation's own wording. Placeholders like {{ member_name }} only."""

    queryset = NotificationTemplate.objects.none()  # schema introspection
    serializer_class = TemplateSerializer
    pagination_class = SmallListPagination
    lookup_value_regex = UUID_LOOKUP
    filter_backends = ()
    required_permissions: ClassVar[dict[str, Any]] = {
        "list": "notifications.template.manage",
        "retrieve": "notifications.template.manage",
        "create": "notifications.template.manage",
        "partial_update": "notifications.template.manage",
        "destroy": "notifications.template.manage",
    }

    def get_queryset(self) -> QuerySet[NotificationTemplate]:
        return services.list_templates()

    @extend_schema(request=TemplateSerializer, responses={201: TemplateSerializer})
    def create(self, request: Request) -> Response:
        data = TemplateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        template = services.save_template(
            actor_id=self.ctx.user_id,
            organisation_id=self.ctx.organisation_id,
            instance=None,
            data=dict(data.validated_data),
        )
        return Response(TemplateSerializer(template).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=TemplateSerializer, responses={200: TemplateSerializer})
    def partial_update(self, request: Request, pk: str) -> Response:
        instance = services.get_template(pk)
        if instance is None:
            raise NotFoundError("Template not found.")
        data = TemplateSerializer(instance, data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        template = services.save_template(
            actor_id=self.ctx.user_id,
            organisation_id=self.ctx.organisation_id,
            instance=instance,
            data=dict(data.validated_data),
        )
        return Response(TemplateSerializer(template).data)

    @extend_schema(responses={204: None})
    def destroy(self, request: Request, pk: str) -> Response:
        services.delete_template(
            actor_id=self.ctx.user_id,
            organisation_id=self.ctx.organisation_id,
            template=self.get_object(),
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
