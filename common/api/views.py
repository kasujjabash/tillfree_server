"""Problem+json responses for errors raised outside DRF (unknown URLs, CSRF failures, crashes)."""

from typing import Any

from django.http import HttpRequest, JsonResponse
from django.utils.translation import gettext_lazy as _

from common.observability.middleware import get_request_id

from .problems import PROBLEM_CONTENT_TYPE, problem_body


def _problem(request: HttpRequest, *, status: int, code: str, title: Any) -> JsonResponse:
    body = problem_body(status=status, code=code, title=title, request_id=get_request_id(request))
    return JsonResponse(body, status=status, content_type=PROBLEM_CONTENT_TYPE)


def bad_request(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    return _problem(request, status=400, code="bad_request", title=_("Bad request."))


def permission_denied(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    return _problem(
        request,
        status=403,
        code="permission_denied",
        title=_("You do not have permission to perform this action."),
    )


def csrf_failure(request: HttpRequest, reason: str = "") -> JsonResponse:
    return _problem(request, status=403, code="csrf_failed", title=_("CSRF verification failed."))


def not_found(request: HttpRequest, exception: Exception | None = None) -> JsonResponse:
    return _problem(request, status=404, code="not_found", title=_("Not found."))


def server_error(request: HttpRequest) -> JsonResponse:
    return _problem(
        request, status=500, code="server_error", title=_("An unexpected error occurred.")
    )
