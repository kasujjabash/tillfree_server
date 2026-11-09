"""Global DRF exception handler producing RFC 9457 ``application/problem+json`` responses."""

from typing import Any

import structlog
from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from django.utils.translation import gettext_lazy as _
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.views import set_rollback

from common.exceptions import DomainError
from common.observability.middleware import get_request_id

from .problems import PROBLEM_CONTENT_TYPE, problem_body

logger = structlog.get_logger("tillfree.api")

# Codes chosen for the public contract where DRF's default code is less clear.
_CODE_OVERRIDES: dict[type[exceptions.APIException], str] = {
    exceptions.ValidationError: "validation_error",
    exceptions.Throttled: "rate_limited",
}


def flatten_validation_errors(detail: Any, prefix: str = "") -> list[dict[str, Any]]:
    """Turn DRF's nested error structure into ``[{field, code, message}]``.

    Field paths use dots for nesting and ``[i]`` for list positions, e.g.
    ``items[2].quantity``. Non-field errors have ``field: null``.
    """
    errors: list[dict[str, Any]] = []
    if isinstance(detail, dict):
        for key, value in detail.items():
            if key == api_settings.NON_FIELD_ERRORS_KEY:
                errors += flatten_validation_errors(value, prefix)
            elif isinstance(key, int):  # errors of a many=True serializer, keyed by position
                errors += flatten_validation_errors(value, f"{prefix}[{key}]")
            else:
                errors += flatten_validation_errors(value, f"{prefix}.{key}" if prefix else key)
    elif isinstance(detail, list):
        for index, item in enumerate(detail):
            if isinstance(item, dict | list):
                errors += flatten_validation_errors(item, f"{prefix}[{index}]")
            else:
                errors += flatten_validation_errors(item, prefix)
    else:
        errors.append(
            {
                "field": prefix or None,
                "code": getattr(detail, "code", None) or "invalid",
                "message": str(detail),
            }
        )
    return errors


def _response(body: dict[str, Any], headers: dict[str, str] | None = None) -> Response:
    return Response(body, status=body["status"], headers=headers, content_type=PROBLEM_CONTENT_TYPE)


def problem_exception_handler(exc: Exception, context: dict[str, Any]) -> Response:
    request = context.get("request")
    request_id = get_request_id(request)
    set_rollback()

    if isinstance(exc, DomainError):
        errors = (
            [{"field": exc.field, "code": exc.code, "message": str(exc.message)}]
            if exc.field
            else None
        )
        return _response(
            problem_body(
                status=exc.status_code,
                code=exc.code,
                title=exc.title,
                detail=None if errors else exc.message,
                errors=errors,
                request_id=request_id,
            )
        )

    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()

    if not isinstance(exc, exceptions.APIException):
        # Logged at ERROR with the traceback, which also reports it to Sentry.
        logger.exception("unhandled_exception", exc_type=type(exc).__name__)
        return _response(
            problem_body(
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                code="server_error",
                title=_("An unexpected error occurred."),
                request_id=request_id,
            )
        )

    headers: dict[str, str] = {}
    if auth_header := getattr(exc, "auth_header", None):
        headers["WWW-Authenticate"] = auth_header
    if (wait := getattr(exc, "wait", None)) is not None:
        headers["Retry-After"] = str(int(wait))

    if isinstance(exc, exceptions.ValidationError):
        body = problem_body(
            status=exc.status_code,
            code="validation_error",
            title=str(exc.default_detail),
            detail=_("One or more fields are invalid."),
            errors=flatten_validation_errors(exc.detail),
            request_id=request_id,
        )
        return _response(body, headers)

    detail = exc.detail
    code = _CODE_OVERRIDES.get(type(exc)) or getattr(detail, "code", None) or exc.default_code
    message = str(detail) if not isinstance(detail, dict | list) else None
    title = str(exc.default_detail)
    body = problem_body(
        status=exc.status_code,
        code=str(code),
        title=title,
        detail=message if message and message != str(title) else None,
        request_id=request_id,
    )
    return _response(body, headers)
