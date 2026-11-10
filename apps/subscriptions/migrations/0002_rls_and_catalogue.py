"""Row-level security for subscription data, and the initial catalogue.

Plan limits are starting values; prices are not seeded (set them per currency in the
admin). A plan without a price in the organisation's currency issues no invoices.
"""

from django.db import migrations

from common.tenancy.operations import EnableTenantRLS

MODULES = [
    ("customers", "Customers", "shared"),
    ("inventory", "Inventory", "shared"),
    ("billing", "Billing and payments", "shared"),
    ("finance", "Finance and reporting", "shared"),
    ("people", "People and workforce", "shared"),
    ("business", "Business operations", "industry"),
    ("healthcare", "Healthcare", "industry"),
]
GB = 1024**3
PLANS = [
    # code, name, public, trial days, limits, modules
    (
        "starter",
        "Starter",
        True,
        30,
        {"max_members": 10, "max_branches": 2, "storage_bytes": 2 * GB},
        ["customers", "inventory", "billing", "finance", "business"],
    ),
    (
        "growth",
        "Growth",
        True,
        30,
        {"max_members": 50, "max_branches": 10, "storage_bytes": 25 * GB},
        ["customers", "inventory", "billing", "finance", "people", "business", "healthcare"],
    ),
    (
        "enterprise",
        "Enterprise",
        False,
        0,
        {"max_members": None, "max_branches": None, "storage_bytes": None},
        [code for code, _name, _kind in MODULES],
    ),
]


def seed(apps, schema_editor):
    Module = apps.get_model("subscriptions", "Module")
    Plan = apps.get_model("subscriptions", "Plan")
    PlanEntitlement = apps.get_model("subscriptions", "PlanEntitlement")
    for code, name, kind in MODULES:
        Module.objects.update_or_create(code=code, defaults={"name": name, "kind": kind})
    for code, name, public, trial, limits, modules in PLANS:
        plan, _ = Plan.objects.update_or_create(
            code=code,
            defaults={"name": name, "is_public": public, "trial_days": trial, "limits": limits},
        )
        for module in modules:
            PlanEntitlement.objects.get_or_create(plan=plan, module_id=module)


class Migration(migrations.Migration):
    dependencies = [("subscriptions", "0001_initial")]

    operations = [
        EnableTenantRLS("Subscription"),
        EnableTenantRLS("EntitlementOverride"),
        EnableTenantRLS("SubscriptionInvoice"),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
