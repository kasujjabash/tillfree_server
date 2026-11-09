from typing import Any, ClassVar
from uuid import UUID

from django.db import models

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel

from .context import get_current_organisation_id
from .exceptions import CrossTenantWrite, TenantContextMissing
from .managers import TenantManager


class TenantModel(UUIDPrimaryKeyModel, TimeStampedModel):
    """Base class for every record owned by one organisation.

    Every concrete subclass's migration must also apply
    ``common.tenancy.operations.EnableTenantRLS``; a test enforces this.

    Managers:
        ``objects``  - tenant-scoped; raises without an active tenant context.
        ``unscoped`` - no application filter (RLS still applies). Only allowlisted
                       modules may use it; a test enforces this.
    """

    organisation = models.ForeignKey(
        "organisations.Organisation",
        on_delete=models.PROTECT,
        related_name="+",
        editable=False,
    )

    objects: ClassVar[TenantManager[Any]] = TenantManager()
    unscoped: ClassVar[models.Manager[Any]] = models.Manager()

    class Meta:
        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        current = get_current_organisation_id()
        if self.organisation_id is None:
            if current is None:
                raise TenantContextMissing(
                    f"Cannot save {type(self).__name__} without an organisation context."
                )
            self.organisation_id = current
        elif current is not None and UUID(str(self.organisation_id)) != current:
            raise CrossTenantWrite(
                f"{type(self).__name__} belongs to another organisation than the active context."
            )
        super().save(*args, **kwargs)
