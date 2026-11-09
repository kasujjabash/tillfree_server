"""Tenancy errors are programming errors (bugs), so they surface as 500s and alerts."""


class TenancyError(Exception):
    pass


class TenantContextMissing(TenancyError):
    """Tenant-scoped data was accessed without an active organisation context."""


class TenantContextConflict(TenancyError):
    """Code tried to switch to a different organisation inside an active tenant context."""


class CrossTenantWrite(TenancyError):
    """A record belonging to one organisation was saved inside another organisation's context."""
