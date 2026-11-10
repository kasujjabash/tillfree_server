from uuid import UUID

from celery import shared_task

from apps.organisations.public import list_organisations
from common.tenancy.context import tenant_context
from common.tenancy.tasks import TenantTask

from . import services
from .models import FileObject


@shared_task(
    base=TenantTask,
    autoretry_for=(ConnectionError, OSError),
    retry_backoff=True,
    retry_backoff_max=900,
    max_retries=8,
)
def verify_file(*, file_id: str, organisation_id: str) -> str:
    file = FileObject.objects.select_for_update().filter(pk=file_id).first()
    if file is None:
        return "missing"
    return services.verify(file).status


@shared_task
def purge_stale_uploads() -> int:
    total = 0
    for organisation_id in list_organisations().values_list("id", flat=True):
        with tenant_context(UUID(str(organisation_id))):
            total += services.purge_stale_uploads()
    return total
