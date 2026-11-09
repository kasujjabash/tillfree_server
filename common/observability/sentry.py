"""Error monitoring. Works with Sentry or any Sentry-compatible service (e.g. GlitchTip)."""

from typing import Any

_DROP_HEADERS = {"authorization", "cookie", "x-csrftoken", "idempotency-key"}


def scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Remove request bodies, cookies, query strings and credential headers."""
    request = event.get("request")
    if isinstance(request, dict):
        for key in ("data", "cookies", "query_string", "env"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                k: v for k, v in headers.items() if k.lower() not in _DROP_HEADERS
            }
    return event


def init_sentry(
    *, dsn: str, environment: str, release: str | None, traces_sample_rate: float
) -> None:
    import sentry_sdk
    from sentry_sdk.integrations.celery import CeleryIntegration
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=scrub_event,  # type: ignore[arg-type]
        integrations=[DjangoIntegration(), CeleryIntegration()],
    )
