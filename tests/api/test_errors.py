"""RFC 9457 problem+json error contract (docs/architecture/08-api.md §4)."""

import pytest
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, Throttled
from rest_framework.permissions import AllowAny
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from common.api.handlers import flatten_validation_errors
from common.exceptions import BusinessRuleViolation, ConflictError

factory = APIRequestFactory()


class LineSerializer(serializers.Serializer):
    product = serializers.UUIDField()
    quantity = serializers.IntegerField(min_value=1)


class OrderSerializer(serializers.Serializer):
    branch = serializers.UUIDField()
    items = LineSerializer(many=True)

    def validate(self, attrs):
        if len(attrs["items"]) > 2:
            raise serializers.ValidationError("Too many items.", code="too_many_items")
        return attrs


def _view(raiser):
    class View(APIView):
        authentication_classes = []
        permission_classes = [AllowAny]

        def post(self, request):
            return raiser(request)

    return View.as_view()


def _call(raiser, data=None):
    response = _view(raiser)(factory.post("/x", data or {}, format="json"))
    response.render()
    return response


def _validate(request):
    OrderSerializer(data=request.data).is_valid(raise_exception=True)


def test_validation_errors_are_flattened_with_paths():
    response = _call(
        _validate,
        {
            "branch": "nope",
            "items": [{"product": "00000000-0000-7000-8000-000000000000", "quantity": 0}],
        },
    )
    assert response.status_code == 400
    assert response["Content-Type"] == "application/problem+json"
    body = response.data
    assert body["code"] == "validation_error"
    assert body["type"] == "urn:tilltree:problem:validation_error"
    fields = {(e["field"], e["code"]) for e in body["errors"]}
    assert ("branch", "invalid") in fields
    assert ("items[0].quantity", "min_value") in fields


def test_non_field_errors_have_null_field():
    items = [{"product": "00000000-0000-7000-8000-000000000000", "quantity": 1}] * 3
    body = _call(_validate, {"branch": "00000000-0000-7000-8000-000000000000", "items": items}).data
    assert body["errors"] == [
        {"field": None, "code": "too_many_items", "message": "Too many items."}
    ]


def test_flatten_handles_plain_lists():
    assert flatten_validation_errors(["a"]) == [{"field": None, "code": "invalid", "message": "a"}]


def test_domain_error_maps_to_problem():
    def raiser(request):
        raise BusinessRuleViolation("Only 3 left.", code="insufficient_stock")

    response = _call(raiser)
    assert response.status_code == 400
    assert response.data["code"] == "insufficient_stock"
    assert response.data["detail"] == "Only 3 left."


def test_domain_error_with_field_becomes_field_error():
    def raiser(request):
        raise ConflictError("Already used.", field="code")

    response = _call(raiser)
    assert response.status_code == 409
    assert response.data["errors"] == [
        {"field": "code", "code": "conflict", "message": "Already used."}
    ]


def test_permission_denied_keeps_custom_code():
    def raiser(request):
        raise PermissionDenied("Module not enabled.", code="module_not_enabled")

    response = _call(raiser)
    assert response.status_code == 403
    assert response.data["code"] == "module_not_enabled"


def test_throttled_sets_retry_after():
    def raiser(request):
        raise Throttled(wait=42)

    response = _call(raiser)
    assert response.status_code == 429
    assert response.data["code"] == "rate_limited"
    assert response["Retry-After"] == "42"


def test_unexpected_exception_returns_500_without_internal_detail():
    def raiser(request):
        raise RuntimeError("database password is hunter2")

    response = _call(raiser)
    assert response.status_code == 500
    assert response.data["code"] == "server_error"
    assert "hunter2" not in str(response.data)


@pytest.mark.django_db
def test_unknown_url_returns_problem_with_request_id(client):
    response = client.get("/api/v1/does-not-exist/")
    assert response.status_code == 404
    assert response["Content-Type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "not_found"
    assert body["request_id"] == response["X-Request-ID"]


@pytest.mark.django_db
def test_openapi_schema_is_generated(client):
    response = client.get("/api/schema/", HTTP_ACCEPT="application/json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Tilltree API"
