from common.permissions import PermissionDef, Sensitivity

PERMISSIONS = [
    PermissionDef("subscriptions.subscription.view", "See the plan, limits and usage"),
    PermissionDef(
        "subscriptions.subscription.manage",
        "Change or cancel the plan",
        Sensitivity.SENSITIVE,
    ),
    PermissionDef(
        "subscriptions.invoice.view",
        "See the organisation's Tillfree invoices",
        Sensitivity.SENSITIVE,
    ),
]
