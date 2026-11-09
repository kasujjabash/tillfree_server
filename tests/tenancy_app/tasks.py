from celery import shared_task

from common.tenancy.tasks import TenantTask

from .models import Widget


@shared_task(base=TenantTask)
def count_widgets(*, organisation_id: str) -> int:
    return Widget.objects.count()
