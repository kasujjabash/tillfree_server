from typing import Any

from django.db import models
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


@extend_schema_field(OpenApiTypes.UUID)
class TenantRelatedField(serializers.PrimaryKeyRelatedField):
    """A reference to another record of the *current* organisation.

    The queryset is built per request through the model's tenant-scoped manager, so an
    ID belonging to another organisation fails validation as "does not exist".
    """

    def __init__(self, model: type[models.Model], **kwargs: Any) -> None:
        self.model = model
        kwargs.setdefault("queryset", None)
        if kwargs.get("read_only"):
            kwargs.pop("queryset")
        super().__init__(**kwargs)

    def get_queryset(self) -> models.QuerySet[Any]:
        return self.model._default_manager.all()
