"""Regression tests for defects found in the Phase 1 code review (2026-11-09)."""

import pytest
from django.db import IntegrityError, connection, transaction
from django.test import Client, override_settings
from rest_framework.test import APIRequestFactory
from rest_framework.throttling import AnonRateThrottle

from apps.organisations.models import OrganisationDeletionNotAllowed
from common.env import env_float
from common.tenancy.context import tenant_context
from tests.tenancy_app.models import Widget


# ---- load-balancer probes use the instance IP as Host, which is not in ALLOWED_HOSTS
@pytest.mark.django_db
@pytest.mark.parametrize("path", ["/health/live/", "/health/ready/"])
def test_health_probes_work_with_unlisted_host(client, path):
    assert client.get(path, HTTP_HOST="10.0.1.23:8000").status_code == 200


@pytest.mark.django_db
def test_other_paths_still_reject_unlisted_host(client):
    assert client.get("/api/v1/nope/", HTTP_HOST="evil.example").status_code == 400


# ---- CSRF failures must use the problem+json contract, not Django's HTML page
@pytest.mark.django_db
def test_csrf_failure_returns_problem_json():
    response = Client(enforce_csrf_checks=True).post("/admin/login/", {"username": "x"})
    assert response.status_code == 403
    assert response["Content-Type"] == "application/problem+json"
    assert response.json()["code"] == "csrf_failed"


# ---- organisation_id given as a string (e.g. from a task kwarg) is the same tenant
def test_string_organisation_id_is_not_treated_as_cross_tenant(org_a):
    with tenant_context(org_a.id):
        widget = Widget(name="x", organisation_id=str(org_a.id))
        widget.save()
    assert widget.pk is not None


# ---- deleting an organisation must fail clearly and never lose tenant data
def test_organisation_delete_is_refused(org_a):
    with tenant_context(org_a.id):
        Widget.objects.create(name="x")
    with pytest.raises(OrganisationDeletionNotAllowed):
        org_a.delete()


def test_database_still_protects_tenant_rows_on_bulk_delete(org_a):
    with tenant_context(org_a.id):
        Widget.objects.create(name="x")
    # A queryset delete bypasses Model.delete(); the foreign key must still hold.
    # Django's PostgreSQL FKs are DEFERRABLE INITIALLY DEFERRED, so in production the
    # violation fails the COMMIT; here we check immediately to observe it in the test.
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        with pytest.raises(IntegrityError), transaction.atomic():
            type(org_a).objects.filter(pk=org_a.pk).delete()


# ---- throttling must not be keyed on a client-controlled X-Forwarded-For
def _ident(num_proxies: int) -> str:
    request = APIRequestFactory().get(
        "/", HTTP_X_FORWARDED_FOR="spoofed-by-client, 203.0.113.9", REMOTE_ADDR="10.0.0.5"
    )
    with override_settings(REST_FRAMEWORK={"NUM_PROXIES": num_proxies}):
        return AnonRateThrottle().get_ident(request)


def test_without_proxies_throttle_uses_socket_address():
    assert _ident(0) == "10.0.0.5"


def test_behind_one_proxy_throttle_uses_address_added_by_proxy():
    assert _ident(1) == "203.0.113.9"


# ---- an empty numeric env var must give a clear configuration error or default
def test_empty_float_env_uses_default(monkeypatch):
    monkeypatch.setenv("TT_F", "")
    assert env_float("TT_F", 0.25) == 0.25
