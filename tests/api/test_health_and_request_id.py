import pytest

from common.health import views as health


def test_live_needs_no_dependencies(client):
    response = client.get("/health/live/")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response["Cache-Control"].startswith("max-age=0")


@pytest.mark.django_db
def test_ready_when_dependencies_are_up(client):
    assert client.get("/health/ready/").json() == {"status": "ok"}


@pytest.mark.django_db
def test_ready_fails_without_revealing_details(client, monkeypatch):
    monkeypatch.setattr(health, "check_database", lambda: False)
    response = client.get("/health/ready/")
    assert response.status_code == 503
    assert response.json() == {"status": "unavailable"}


@pytest.mark.django_db
def test_valid_incoming_request_id_is_echoed(client):
    response = client.get("/api/v1/nope/", HTTP_X_REQUEST_ID="abc-123_DEF.456")
    assert response["X-Request-ID"] == "abc-123_DEF.456"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad", ["short", "has space here", "x" * 65, "line\nbreak-injection", "abcdefgh\n"]
)
def test_unsafe_incoming_request_id_is_replaced(client, bad):
    response = client.get("/api/v1/nope/", HTTP_X_REQUEST_ID=bad)
    assert response.status_code == 404
    assert response["X-Request-ID"] != bad
    assert len(response["X-Request-ID"]) == 36  # generated UUID
