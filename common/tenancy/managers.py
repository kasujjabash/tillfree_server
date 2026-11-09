from typing import TypeVar

from django.db import models

from .context import get_current_organisation_id
from .exceptions import TenantContextMissing

_M = TypeVar("_M", bound=models.Model)


class TenantManager(models.Manager[_M]):
    """Default manager for tenant-owned models.

    Returns only the current organisation's rows, and raises instead of returning
    every tenant's rows when no organisation context is active (fail closed).

    Querysets are bound to the organisation active when they are created, so never
    build tenant querysets at import time (e.g. as class attributes of serializers);
    build them per request instead.
    """

    def get_queryset(self) -> models.QuerySet[_M]:
        organisation_id = get_current_organisation_id()
        if organisation_id is None:
            raise TenantContextMissing(
                f"{self.model.__name__} is tenant-scoped; use tenant_context() first."
            )
        return super().get_queryset().filter(organisation_id=organisation_id)
