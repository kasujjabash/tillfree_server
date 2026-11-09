"""Base classes for organisation-scoped (tenant) API views.

Every tenant view:
1. reads ``X-Organisation-ID`` and runs the whole request in that tenant's
   transaction (``tenant_context``), so row-level security applies to every query;
2. authenticates, then resolves the caller's membership (or support grant) into an
   ``AccessContext`` (``self.ctx``) — non-members get 404;
3. enforces the organisation's two-step verification policy;
4. checks the permission declared for the action in ``required_permissions``.
   An action without a declaration is a configuration error, never "allowed".
"""

from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any, ClassVar
from uuid import UUID

from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.http import HttpRequest, HttpResponseBase
from django.utils.translation import gettext_lazy as _
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.views import APIView

from apps.identity.public import AuthInfo, User
from common.api.request import client_ip
from common.exceptions import BusinessRuleViolation, NotFoundError, PermissionDeniedError
from common.tenancy.context import tenant_context, user_context

from .. import selectors
from ..authorization import AccessContext, MFARequired, authorize, authorize_anywhere
from ..context import build_access_context
from ..services.support import record_support_request

ORGANISATION_HEADER = "X-Organisation-ID"
_INVALID = object()


@dataclass(frozen=True)
class AnyBranch:
    """Permission held in at least one branch; the view narrows to permitted branches."""

    code: str


Requirement = str | AnyBranch | None  # str = organisation-wide; None = any member


class TenantAPIMixin:
    required_permissions: ClassVar[dict[str, Requirement]] = {}
    ctx: AccessContext

    def dispatch(self, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponseBase:
        raw = request.headers.get(ORGANISATION_HEADER)
        organisation_id: object = None
        if raw:
            try:
                organisation_id = UUID(raw)
            except ValueError:
                organisation_id = _INVALID
        with ExitStack() as stack:
            if isinstance(organisation_id, UUID):
                stack.enter_context(tenant_context(organisation_id))
            else:
                stack.enter_context(transaction.atomic())
            self._context_stack = stack
            self._organisation_id = organisation_id
            return super().dispatch(request, *args, **kwargs)  # type: ignore[misc]

    def initial(self, request: Request, *args: Any, **kwargs: Any) -> None:
        super().initial(request, *args, **kwargs)  # type: ignore[misc]  # auth + throttles
        if self._organisation_id is None:
            raise BusinessRuleViolation(
                _("Send the X-Organisation-ID header to choose an organisation."),
                code="organisation_required",
            )
        if self._organisation_id is _INVALID:
            raise NotFoundError(_("Organisation not found."))
        user = request.user
        organisation_id = self._organisation_id
        if not isinstance(user, User) or not isinstance(organisation_id, UUID):
            raise NotAuthenticated()
        self._context_stack.enter_context(user_context(user.id))
        auth = request.auth if isinstance(request.auth, AuthInfo) else None
        self.ctx = build_access_context(
            user=user,
            organisation_id=organisation_id,
            auth=auth,
            ip_address=client_ip(request),
        )
        if self.ctx.require_mfa and not self.ctx.mfa_satisfied:
            raise MFARequired(
                _("This organisation requires two-step verification. Turn it on and sign in again.")
            )
        if self.ctx.is_support:
            if request.method not in SAFE_METHODS:
                raise PermissionDeniedError(
                    _("Support access is read-only."), code="support_read_only"
                )
            record_support_request(self.ctx, method=str(request.method), path=request.path)
        self._check_declared_permission(request)

    def _check_declared_permission(self, request: Request) -> None:
        method = str(request.method).lower()
        if method not in self.http_method_names or not hasattr(self, method):  # type: ignore[attr-defined]
            return  # DRF answers 405 Method Not Allowed
        key = getattr(self, "action", None) or method
        if key in ("metadata", "options"):
            return
        if key not in self.required_permissions:
            raise ImproperlyConfigured(f"{type(self).__name__} declares no permission for '{key}'.")
        requirement = self.required_permissions[key]
        if requirement is None:
            return
        if isinstance(requirement, AnyBranch):
            authorize_anywhere(self.ctx, requirement.code)
        else:
            authorize(self.ctx, requirement)


class TenantAPIView(TenantAPIMixin, APIView):
    permission_classes = (IsAuthenticated,)


class IsPlatformStaff(BasePermission):
    """Platform endpoints: active platform staff, signed in with two-step verification."""

    message = _("Platform staff with two-step verification only.")

    def has_permission(self, request: Request, view: Any) -> bool:
        user = request.user
        if not isinstance(user, User) or not isinstance(request.auth, AuthInfo):
            return False
        return request.auth.mfa_satisfied and selectors.platform_staff(user) is not None
