"""Exceptions raised by domain services and translated to HTTP by the API layer.

Services raise these instead of DRF exceptions, so they stay usable from Celery
tasks and management commands. See docs/architecture/08-api.md §4.
"""

from django.utils.functional import Promise
from django.utils.translation import gettext_lazy as _


class DomainError(Exception):
    status_code = 400
    default_code = "business_rule_violation"
    title: str | Promise = _("The request breaks a business rule.")

    def __init__(
        self, message: str | Promise, *, code: str | None = None, field: str | None = None
    ) -> None:
        super().__init__(str(message))
        self.message = message
        self.code = code or self.default_code
        self.field = field


class BusinessRuleViolation(DomainError):
    pass


class NotFoundError(DomainError):
    status_code = 404
    default_code = "not_found"
    title = _("Not found.")


class ConflictError(DomainError):
    status_code = 409
    default_code = "conflict"
    title = _("The request conflicts with the current state of the resource.")


class PermissionDeniedError(DomainError):
    status_code = 403
    default_code = "permission_denied"
    title = _("You do not have permission to perform this action.")


class RateLimitedError(DomainError):
    status_code = 429
    default_code = "rate_limited"
    title = _("Too many attempts. Try again later.")

    def __init__(
        self, message: str | Promise, *, retry_after: int, code: str | None = None
    ) -> None:
        super().__init__(message, code=code)
        self.retry_after = retry_after
