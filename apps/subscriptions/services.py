"""Subscription lifecycle, entitlement resolution and billing records.

Functions that touch tenant data run inside the organisation's ``tenant_context``.
"""

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import AuditEvent, record
from apps.organisations.public import Organisation, get_organisation
from common.db.ids import uuid7
from common.exceptions import BusinessRuleViolation, ConflictError, NotFoundError
from common.tenancy.context import tenant_context

from .entitlements import NO_SUBSCRIPTION_LIMITS, Entitlements, validate_limits
from .models import (
    CURRENT_STATUSES,
    EntitlementOverride,
    Interval,
    Module,
    Plan,
    PlanPrice,
    Subscription,
    SubscriptionInvoice,
)

logger = structlog.get_logger("tillfree.subscriptions")

INVOICE_DUE_DAYS = 14


def _period_end(start: datetime, interval: str) -> datetime:
    return start + (timedelta(days=365) if interval == Interval.YEAR else timedelta(days=30))


# ------------------------------------------------------------------ reads
def current_subscription() -> Subscription | None:
    """The current subscription of the organisation in context."""
    return Subscription.objects.select_related("plan").filter(status__in=CURRENT_STATUSES).first()


def entitlements_for(organisation_id: UUID) -> Entitlements:
    """Modules and limits for the organisation in context (plan + active overrides)."""
    subscription = current_subscription()
    if subscription is None:
        modules: set[str] = set()
        limits: dict[str, int | None] = dict(NO_SUBSCRIPTION_LIMITS)
        plan_code = status = None
    else:
        plan = subscription.plan
        modules = set(
            plan.entitlements.filter(module__is_active=True).values_list("module_id", flat=True)
        )
        limits = {**NO_SUBSCRIPTION_LIMITS, **plan.limits}
        plan_code, status = plan.code, subscription.status

    applied: list[str] = []
    for override in EntitlementOverride.objects.order_by("created_at"):
        if not override.is_active():
            continue
        if override.module_id and override.enabled is not None:
            (modules.add if override.enabled else modules.discard)(override.module_id)
        limits.update(override.limits)
        applied.append(str(override.id))
    return Entitlements(
        modules=frozenset(modules),
        limits=limits,
        plan_code=plan_code,
        status=status,
        overrides=tuple(applied),
    )


def public_plans() -> list[Plan]:
    return list(
        Plan.objects.filter(is_public=True, is_active=True).prefetch_related(
            "prices", "entitlements"
        )
    )


def list_invoices() -> Any:
    return SubscriptionInvoice.objects.select_related("subscription__plan").order_by(
        "-period_start"
    )


# ------------------------------------------------------------------ lifecycle
def start_trial(organisation: Organisation) -> Subscription:
    """Called when an organisation is created (inside its tenant context)."""
    plan = Plan.objects.filter(code=settings.DEFAULT_PLAN_CODE, is_active=True).first()
    if plan is None:
        raise BusinessRuleViolation(_("No default plan is configured."), code="no_default_plan")
    now = timezone.now()
    trial_end = now + timedelta(days=plan.trial_days)
    return Subscription.objects.create(
        plan=plan,
        status=Subscription.Status.TRIALING,
        current_period_start=now,
        current_period_end=trial_end if plan.trial_days else _period_end(now, Interval.MONTH),
        trial_ends_at=trial_end if plan.trial_days else None,
    )


def _require_current() -> Subscription:
    subscription = current_subscription()
    if subscription is None:
        raise NotFoundError(
            _("The organisation has no current subscription."), code="no_subscription"
        )
    return subscription


@transaction.atomic
def change_plan(
    *, organisation_id: UUID, plan_code: str, interval: str, actor_id: UUID
) -> Subscription:
    """Switch plan immediately (no proration yet). Limits are checked by the caller."""
    plan = Plan.objects.filter(code=plan_code, is_active=True, is_public=True).first()
    if plan is None:
        raise BusinessRuleViolation(_("Unknown plan."), code="unknown_plan", field="plan_code")
    subscription = _require_current()
    previous = subscription.plan.code
    subscription.plan = plan
    subscription.interval = interval
    subscription.cancel_at_period_end = False
    subscription.save(update_fields=["plan", "interval", "cancel_at_period_end", "updated_at"])
    record(
        "subscription.plan_changed",
        organisation_id=organisation_id,
        actor_id=actor_id,
        resource_type="subscription",
        resource_id=subscription.id,
        metadata={"from": previous, "to": plan.code, "interval": interval},
    )
    return subscription


@transaction.atomic
def set_cancel_at_period_end(
    *, organisation_id: UUID, cancel: bool, actor_id: UUID
) -> Subscription:
    subscription = _require_current()
    subscription.cancel_at_period_end = cancel
    subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
    record(
        "subscription.cancellation_scheduled" if cancel else "subscription.cancellation_withdrawn",
        organisation_id=organisation_id,
        actor_id=actor_id,
        resource_type="subscription",
        resource_id=subscription.id,
    )
    return subscription


def _issue_invoice(
    subscription: Subscription, organisation: Organisation
) -> SubscriptionInvoice | None:
    price = PlanPrice.objects.filter(
        plan=subscription.plan, currency=organisation.base_currency, interval=subscription.interval
    ).first()
    if price is None or price.amount == 0:
        return None  # free, or not priced in this currency: nothing is owed
    start = subscription.current_period_start
    invoice, _created = SubscriptionInvoice.objects.get_or_create(
        subscription=subscription,
        period_start=start,
        defaults={
            "number": f"TF-{start:%Y%m}-{str(uuid7())[-12:].upper()}",
            "period_end": subscription.current_period_end,
            "amount": price.amount,
            "currency": price.currency,
            "due_at": start + timedelta(days=INVOICE_DUE_DAYS),
        },
    )
    return invoice


def renew_if_due(organisation_id: UUID, *, now: datetime | None = None) -> str:
    """Advance the billing period when it has ended. Idempotent; safe to run repeatedly.

    Returns what happened: "not_due", "cancelled", "renewed" or "none".
    """
    now = now or timezone.now()
    with tenant_context(organisation_id), transaction.atomic():
        subscription = (
            Subscription.objects.select_for_update()
            .select_related("plan")
            .filter(status__in=CURRENT_STATUSES)
            .first()
        )
        if subscription is None:
            return "none"
        overdue = SubscriptionInvoice.objects.filter(
            subscription=subscription, status=SubscriptionInvoice.Status.ISSUED, due_at__lt=now
        ).exists()
        if overdue and subscription.status == Subscription.Status.ACTIVE:
            subscription.status = Subscription.Status.PAST_DUE
            subscription.save(update_fields=["status", "updated_at"])
        if subscription.current_period_end > now:
            return "not_due"
        if subscription.cancel_at_period_end:
            subscription.status = Subscription.Status.CANCELLED
            subscription.cancelled_at = now
            subscription.save(update_fields=["status", "cancelled_at", "updated_at"])
            record(
                "subscription.cancelled",
                organisation_id=organisation_id,
                actor_type=AuditEvent.ActorType.SYSTEM,
                resource_type="subscription",
                resource_id=subscription.id,
            )
            return "cancelled"
        if subscription.status == Subscription.Status.TRIALING:
            subscription.status = Subscription.Status.ACTIVE
        subscription.current_period_start = subscription.current_period_end
        subscription.current_period_end = _period_end(
            subscription.current_period_start, subscription.interval
        )
        subscription.save()
        organisation = get_organisation(organisation_id)
        invoice = _issue_invoice(subscription, organisation) if organisation else None
        record(
            "subscription.renewed",
            organisation_id=organisation_id,
            actor_type=AuditEvent.ActorType.SYSTEM,
            resource_type="subscription",
            resource_id=subscription.id,
            metadata={"invoice": invoice.number if invoice else None},
        )
        return "renewed"


# ------------------------------------------------------------------ platform operations
@transaction.atomic
def grant_override(
    *,
    organisation_id: UUID,
    module_code: str | None,
    enabled: bool | None,
    limits: dict[str, Any],
    reason: str,
    granted_by: Any,
    expires_at: datetime | None = None,
) -> EntitlementOverride:
    if module_code and not Module.objects.filter(code=module_code).exists():
        raise BusinessRuleViolation(_("Unknown module."), code="unknown_module", field="module")
    try:
        clean_limits = validate_limits(limits)
    except ValueError as exc:
        raise BusinessRuleViolation(str(exc), code="invalid_limits", field="limits") from exc
    if get_organisation(organisation_id) is None:
        raise NotFoundError(_("Organisation not found."))
    with tenant_context(organisation_id):
        override = EntitlementOverride.objects.create(
            module_id=module_code,
            enabled=enabled,
            limits=clean_limits,
            reason=reason,
            granted_by=granted_by,
            expires_at=expires_at,
        )
        record(
            "subscription.override_granted",
            organisation_id=organisation_id,
            actor_id=granted_by.id,
            actor_type=AuditEvent.ActorType.PLATFORM_STAFF,
            resource_type="entitlement_override",
            resource_id=override.id,
            metadata={
                "module": module_code,
                "enabled": enabled,
                "limits": clean_limits,
                "reason": reason,
            },
        )
    return override


@transaction.atomic
def mark_invoice_paid(
    *, organisation_id: UUID, invoice_id: UUID, actor: Any
) -> SubscriptionInvoice:
    with tenant_context(organisation_id):
        invoice = SubscriptionInvoice.objects.select_for_update().filter(pk=invoice_id).first()
        if invoice is None:
            raise NotFoundError(_("Invoice not found."))
        if invoice.status != SubscriptionInvoice.Status.ISSUED:
            raise ConflictError(_("Only issued invoices can be marked paid."), code="not_issued")
        invoice.status = SubscriptionInvoice.Status.PAID
        invoice.paid_at = timezone.now()
        invoice.save(update_fields=["status", "paid_at", "updated_at"])
        subscription = invoice.subscription
        still_overdue = SubscriptionInvoice.objects.filter(
            subscription=subscription,
            status=SubscriptionInvoice.Status.ISSUED,
            due_at__lt=timezone.now(),
        ).exists()
        if subscription.status == Subscription.Status.PAST_DUE and not still_overdue:
            subscription.status = Subscription.Status.ACTIVE
            subscription.save(update_fields=["status", "updated_at"])
        record(
            "subscription.invoice_paid",
            organisation_id=organisation_id,
            actor_id=actor.id,
            actor_type=AuditEvent.ActorType.PLATFORM_STAFF,
            resource_type="subscription_invoice",
            resource_id=invoice.id,
        )
    return invoice
