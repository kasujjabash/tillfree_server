"""RFC 9457 Problem Details bodies, shared by the DRF handler and plain Django error views."""

from typing import Any

from django.conf import settings
from django.utils.functional import Promise

PROBLEM_CONTENT_TYPE = "application/problem+json"


def problem_body(
    *,
    status: int,
    code: str,
    title: str | Promise,
    detail: str | Promise | None = None,
    errors: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"{settings.API_PROBLEM_TYPE_BASE}{code}",
        "title": str(title),
        "status": status,
        "code": code,
    }
    if detail:
        body["detail"] = str(detail)
    if errors:
        body["errors"] = errors
    if request_id:
        body["request_id"] = request_id
    return body
