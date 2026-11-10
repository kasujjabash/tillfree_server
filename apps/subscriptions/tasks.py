from uuid import UUID

from celery import shared_task

from apps.organisations.public import list_organisations

from .services import renew_if_due


@shared_task
def renew_due_subscriptions() -> int:
    """Daily: one renewal task per organisation, each in its own tenant context."""
    count = 0
    for organisation_id in (
        list_organisations().filter(status="active").values_list("id", flat=True)
    ):
        renew_subscription.delay(organisation_id=str(organisation_id))
        count += 1
    return count


@shared_task(acks_late=True)
def renew_subscription(*, organisation_id: str) -> str:
    return renew_if_due(UUID(organisation_id))
