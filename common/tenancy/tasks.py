from typing import Any
from uuid import UUID

from celery import Task

from .context import tenant_context
from .exceptions import TenantContextMissing


class TenantTask(Task):
    """Base class for Celery tasks that touch tenant data.

    The organisation is an explicit keyword argument, never implicit state::

        @shared_task(base=TenantTask)
        def send_low_stock_alerts(*, organisation_id: str) -> None: ...

        send_low_stock_alerts.delay(organisation_id=str(org.id))

    The whole task runs inside ``tenant_context`` (one transaction). A task that must
    call a slow external service should instead load what it needs inside
    ``tenant_context`` and make the external call after the block.
    """

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raw = kwargs.get("organisation_id")
        if not raw:
            raise TenantContextMissing(f"Task {self.name} requires an organisation_id keyword.")
        with tenant_context(UUID(str(raw))):
            return super().__call__(*args, **kwargs)
