import re
import time
from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse

from common.db.ids import uuid7

REQUEST_ID_HEADER = "X-Request-ID"
# Accept a caller's request ID only if it is safe to log (no spaces, newlines, or huge values).
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

logger = structlog.get_logger("tilltree.request")


def get_request_id(request: HttpRequest | None = None) -> str | None:
    if request is not None and (request_id := getattr(request, "request_id", None)):
        return str(request_id)
    value = structlog.contextvars.get_contextvars().get("request_id")
    return str(value) if value else None


class RequestIDMiddleware:
    """Assigns a request ID, binds it to the log context, logs the request, echoes the header.

    Only the path is logged, never the query string, because query strings can carry
    personal data or tokens.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.fullmatch(incoming) else str(uuid7())
        request.request_id = request_id  # type: ignore[attr-defined]

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started = time.monotonic()
        try:
            response = self.get_response(request)
            response[REQUEST_ID_HEADER] = request_id
            logger.info(
                "request_finished",
                method=request.method,
                path=request.path,
                status=response.status_code,
                duration_ms=round((time.monotonic() - started) * 1000, 1),
            )
            return response
        finally:
            structlog.contextvars.clear_contextvars()
