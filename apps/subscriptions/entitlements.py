"""What an organisation may use: modules and usage limits.

Foundation modules (identity, organisations, access, ...) are always available. Every
other module must be explicitly entitled; an unknown module is treated as NOT enabled
(fail closed), so a new module can never be used before it is sold or granted.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from django.utils.translation import gettext_lazy as _

from common.exceptions import PermissionDeniedError

PLATFORM_MODULES = frozenset(
    {
        "identity",
        "organisations",
        "access",
        "audit",
        "localisation",
        "subscriptions",
        "notifications",
        "documents",
        "events",
    }
)

# Known limits and what they count. ``None`` means unlimited.
LIMITS: Mapping[str, str] = MappingProxyType(
    {
        "max_members": "Active members and pending invitations",
        "max_branches": "Active branches",
        "storage_bytes": "Total size of stored files",
    }
)

# Applies when an organisation has no current subscription.
NO_SUBSCRIPTION_LIMITS: Mapping[str, int | None] = MappingProxyType(
    {"max_members": 3, "max_branches": 1, "storage_bytes": 100 * 1024 * 1024}
)


class PlanLimitReached(PermissionDeniedError):
    default_code = "plan_limit_reached"
    title = _("Your plan's limit has been reached. Upgrade to continue.")


@dataclass(frozen=True)
class Entitlements:
    modules: frozenset[str]
    limits: Mapping[str, int | None]
    plan_code: str | None = None
    status: str | None = None
    overrides: tuple[str, ...] = field(default=())

    def has_module(self, module: str) -> bool:
        return module in PLATFORM_MODULES or module in self.modules

    def limit(self, key: str) -> int | None:
        if key not in LIMITS:
            raise KeyError(f"Unknown limit {key!r}")
        return self.limits.get(key)


def ensure_within_limit(
    entitlements: Entitlements, key: str, *, current: int, adding: int = 1
) -> None:
    """Raise ``PlanLimitReached`` if ``current + adding`` would exceed the limit."""
    limit = entitlements.limit(key)
    if limit is not None and current + adding > limit:
        raise PlanLimitReached(
            _("Your plan allows %(limit)s for %(what)s.")
            % {"limit": limit, "what": LIMITS[key].lower()},
            code="plan_limit_reached",
        )


def validate_limits(limits: Mapping[str, object]) -> dict[str, int | None]:
    clean: dict[str, int | None] = {}
    for key, value in limits.items():
        if key not in LIMITS:
            raise ValueError(f"Unknown limit {key!r}")
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 0
        ):
            raise ValueError(f"Limit {key!r} must be a non-negative integer or null")
        clean[key] = value
    return clean
