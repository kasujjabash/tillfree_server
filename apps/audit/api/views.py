from typing import Any, ClassVar

from django.db.models import QuerySet
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, serializers, viewsets

from apps.access.public import TenantAPIMixin

from ..models import AuditEvent
from ..selectors import organisation_events


class AuditEventSerializer(serializers.ModelSerializer[AuditEvent]):
    class Meta:
        model = AuditEvent
        fields = (
            "id",
            "occurred_at",
            "actor_id",
            "actor_type",
            "action",
            "resource_type",
            "resource_id",
            "metadata",
            "request_id",
            "ip_address",
        )


class AuditQuerySerializer(serializers.Serializer[Any]):
    action = serializers.CharField(required=False, max_length=100)
    actor_id = serializers.UUIDField(required=False)
    resource_type = serializers.CharField(required=False, max_length=100)
    resource_id = serializers.CharField(required=False, max_length=64)
    occurred_after = serializers.DateTimeField(required=False)
    occurred_before = serializers.DateTimeField(required=False)


@extend_schema(
    parameters=[
        OpenApiParameter(name, str, required=False)
        for name in (
            "action",
            "actor_id",
            "resource_type",
            "resource_id",
            "occurred_after",
            "occurred_before",
        )
    ]
)
class AuditEventViewSet(TenantAPIMixin, mixins.ListModelMixin, viewsets.GenericViewSet[AuditEvent]):
    """The organisation's audit log, newest first (cursor pagination)."""

    serializer_class = AuditEventSerializer
    filter_backends = ()
    required_permissions: ClassVar[dict[str, Any]] = {"list": "audit.event.view"}

    def get_queryset(self) -> QuerySet[AuditEvent]:
        query = AuditQuerySerializer(data=self.request.query_params)
        query.is_valid(raise_exception=True)
        return organisation_events(self.ctx.organisation_id, **query.validated_data)
