from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import transaction
from django.utils import timezone

from apps.access.authorization import ModuleNotEnabled, authorize
from apps.access.context import build_access_context
from apps.subscriptions.models import (
    Module,
    Plan,
    PlanPrice,
    Subscription,
    SubscriptionInvoice,
)
from apps.subscriptions.services import entitlements_for, renew_if_due
from common.tenancy.context import tenant_context, user_context

pytestmark = pytest.mark.django_db


def _subscription(org) -> Subscription:
    with tenant_context(org.id):
        return Subscription.objects.select_related("plan").get()


def test_new_organisation_starts_a_trial(new_org, client_for):
    org, owner = new_org()
    sub = _subscription(org)
    assert (sub.plan.code, sub.status) == ("starter", "trialing")
    body = client_for(owner, org).get("/api/v1/subscription/").json()
    usage = {u["key"]: (u["used"], u["limit"]) for u in body["usage"]}
    assert usage["max_members"] == (1, 10)
    assert usage["max_branches"] == (0, 2)
    assert "inventory" in body["modules"]


def test_branch_limit_is_enforced(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    for name in ("One", "Two"):
        assert client.post("/api/v1/branches/", {"name": name}, format="json").status_code == 201
    third = client.post("/api/v1/branches/", {"name": "Three"}, format="json")
    assert third.status_code == 403 and third.json()["code"] == "plan_limit_reached"


def test_member_limit_counts_pending_invitations(new_org, client_for):
    org, owner = new_org()
    with tenant_context(org.id):
        Plan.objects.filter(code="starter").update(limits={"max_members": 2})
    client = client_for(owner, org)
    assert (
        client.post(
            "/api/v1/invitations/",
            {"email": "a@example.com", "template_key": "staff"},
            format="json",
        ).status_code
        == 201
    )
    blocked = client.post(
        "/api/v1/invitations/", {"email": "b@example.com", "template_key": "staff"}, format="json"
    )
    assert blocked.status_code == 403 and blocked.json()["code"] == "plan_limit_reached"


def test_platform_override_raises_a_limit(new_org, make_staff, client_for):
    org, owner = new_org()
    staff = make_staff()
    response = client_for(staff, mfa=True).post(
        f"/api/v1/platform/organisations/{org.id}/entitlement-overrides/",
        {"limits": {"max_branches": 5}, "reason": "Pilot customer"},
        format="json",
    )
    assert response.status_code == 201
    client = client_for(owner, org)
    for name in ("A", "B", "C"):
        assert client.post("/api/v1/branches/", {"name": name}, format="json").status_code == 201


def test_override_endpoint_needs_platform_staff(new_org, client_for):
    org, owner = new_org()
    response = client_for(owner, mfa=True).post(
        f"/api/v1/platform/organisations/{org.id}/entitlement-overrides/",
        {"limits": {"max_branches": 99}, "reason": "self-service upgrade"},
        format="json",
    )
    assert response.status_code == 403


def test_modules_outside_the_plan_are_refused(new_org):
    """tests.tenancy_app declares 'tenancy_app.widget.view': an unsold module (fail closed)."""
    org, owner = new_org()
    with tenant_context(org.id), user_context(owner.id):
        ctx = build_access_context(user=owner, organisation_id=org.id, auth=None, ip_address=None)
        with pytest.raises(ModuleNotEnabled):
            authorize(ctx, "tenancy_app.widget.view")
        Module.objects.create(code="tenancy_app", name="Widgets", kind="shared")
        Plan.objects.get(code="starter").entitlements.create(module_id="tenancy_app")
        ctx = build_access_context(user=owner, organisation_id=org.id, auth=None, ip_address=None)
        authorize(ctx, "tenancy_app.widget.view")


def test_only_owners_change_the_plan(new_org, add_member, client_for):
    org, owner = new_org()
    manager, _ = add_member(org, "m@example.com", "manager")
    assert client_for(manager, org).get("/api/v1/subscription/").status_code == 200
    denied = client_for(manager, org).post(
        "/api/v1/subscription/change-plan/", {"plan_code": "growth"}, format="json"
    )
    assert denied.status_code == 403
    ok = client_for(owner, org).post(
        "/api/v1/subscription/change-plan/", {"plan_code": "growth"}, format="json"
    )
    assert ok.status_code == 200
    assert _subscription(org).plan.code == "growth"
    hidden = client_for(owner, org).post(
        "/api/v1/subscription/change-plan/", {"plan_code": "enterprise"}, format="json"
    )
    assert hidden.status_code == 400  # not a public plan


def test_renewal_ends_trial_issues_invoice_and_handles_payment(new_org, make_staff, client_for):
    org, owner = new_org()
    PlanPrice.objects.create(
        plan=Plan.objects.get(code="starter"),
        currency="UGX",
        amount=Decimal("150000"),
        interval="month",
    )
    with tenant_context(org.id):
        Subscription.objects.update(
            current_period_start=timezone.now() - timedelta(days=30),
            current_period_end=timezone.now() - timedelta(minutes=1),
        )
    assert renew_if_due(org.id) == "renewed"
    sub = _subscription(org)
    assert sub.status == "active"
    with tenant_context(org.id):
        invoice = SubscriptionInvoice.objects.get()
    assert (invoice.amount, invoice.currency) == (Decimal("150000"), "UGX")
    assert renew_if_due(org.id) == "not_due"  # idempotent

    # Unpaid past the due date: past due.
    with tenant_context(org.id):
        SubscriptionInvoice.objects.update(due_at=timezone.now() - timedelta(days=1))
    renew_if_due(org.id)
    assert _subscription(org).status == "past_due"

    staff = make_staff()
    paid = client_for(staff, mfa=True).post(
        f"/api/v1/platform/organisations/{org.id}/invoices/{invoice.id}/mark-paid/"
    )
    assert paid.status_code == 200
    assert _subscription(org).status == "active"
    invoices = client_for(owner, org).get("/api/v1/subscription/invoices/").json()["results"]
    assert invoices[0]["status"] == "paid"


def test_cancellation_takes_effect_at_period_end(new_org, client_for):
    org, owner = new_org()
    assert client_for(owner, org).post("/api/v1/subscription/cancellation/").status_code == 200
    assert renew_if_due(org.id) == "not_due"
    with tenant_context(org.id):
        Subscription.objects.update(
            current_period_start=timezone.now() - timedelta(days=30),
            current_period_end=timezone.now() - timedelta(seconds=1),
        )
    assert renew_if_due(org.id) == "cancelled"
    with tenant_context(org.id), transaction.atomic():
        ent = entitlements_for(org.id)
    assert ent.status is None and ent.limit("max_branches") == 1  # falls back to minimal limits
