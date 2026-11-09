import time

import pytest
import structlog
from celery import Celery
from celery.contrib.testing.worker import start_worker

from common.observability.redaction import REDACTED, redact_sensitive
from common.observability.sentry import scrub_event


def test_redaction_removes_secrets_at_any_depth():
    event = {
        "event": "login_failed",
        "password": "hunter2",
        "user_id": "u1",
        "payload": {"refresh_token": "abc", "items": [{"api_key": "k", "qty": 1}]},
    }
    result = redact_sensitive(None, "info", event)
    assert result["event"] == "login_failed"
    assert result["password"] == REDACTED
    assert result["user_id"] == "u1"
    assert result["payload"]["refresh_token"] == REDACTED
    assert result["payload"]["items"][0] == {"api_key": REDACTED, "qty": 1}


def test_sentry_scrubbing_drops_bodies_and_credentials():
    event = {
        "request": {
            "data": {"password": "x"},
            "cookies": {"sessionid": "y"},
            "query_string": "token=z",
            "headers": {"Authorization": "Bearer t", "User-Agent": "ua"},
        }
    }
    request = scrub_event(event, {})["request"]
    assert set(request) == {"headers"}
    assert request["headers"] == {"User-Agent": "ua"}


@pytest.mark.integration
def test_request_id_travels_from_publisher_to_worker():
    app = Celery("propagation-test", broker="memory://", fixups=[])
    app.conf.task_ignore_result = True
    seen: list[object] = []

    @app.task(name="record_request_id")
    def record_request_id() -> None:
        seen.append(structlog.contextvars.get_contextvars().get("request_id"))

    with start_worker(app, pool="solo", perform_ping_check=False, shutdown_timeout=10):
        structlog.contextvars.bind_contextvars(request_id="req-0001-abcd")
        try:
            record_request_id.delay()
        finally:
            structlog.contextvars.clear_contextvars()
        deadline = time.monotonic() + 10
        while not seen and time.monotonic() < deadline:
            time.sleep(0.05)

    assert seen == ["req-0001-abcd"]


def test_json_tracebacks_never_include_local_variables():
    from common.observability.logging import json_tracebacks

    def failing_login() -> None:
        password = "hunter2"  # noqa: F841  (a local that must not be logged)
        raise ValueError("boom")

    try:
        failing_login()
    except ValueError as exc:
        event = json_tracebacks()(None, "error", {"event": "x", "exc_info": exc})
    assert "hunter2" not in str(event)
    assert event["exception"][0]["exc_type"] == "ValueError"
