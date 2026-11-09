"""Cross-tenant and IDOR attacks against every tenant endpoint (03-multi-tenancy.md §8).

Org A's Owner holds every permission in A. Even so, nothing of Org B may be read,
changed or referenced, whether by guessing IDs or by sending B's header.
"""

import pytest
from django.urls import URLPattern, URLResolver, get_resolver

from apps.access.api.base import TenantAPIMixin
from apps.access.models import Invitation, Membership, Role, RoleAssignment, SupportAccessGrant
from apps.organisations.models import Branch, Department
from common.tenancy.context import tenant_context

pytestmark = [pytest.mark.security, pytest.mark.django_db]


@pytest.fixture
def two_orgs(new_org, make_staff):
    org_a, owner_a = new_org("Org A", "owner-a@example.com")
    org_b, owner_b = new_org("Org B", "owner-b@example.com")
    with tenant_context(org_b.id):
        branch = Branch.objects.create(
            name="B branch", country_code="UG", timezone="Africa/Kampala"
        )
        objects = {
            "branches": branch,
            "departments": Department.objects.create(name="B dept"),
            "memberships": Membership.objects.get(user=owner_b),
            "roles": Role.objects.create(name="B role", permissions=[]),
            "role-assignments": RoleAssignment.objects.get(membership__user=owner_b),
            "invitations": Invitation.objects.create(
                email="x@example.com",
                token_hash="h" * 64,
                template_key="staff",
                expires_at="2099-01-01T00:00:00Z",
            ),
            "support-grants": SupportAccessGrant.objects.create(
                staff_user=make_staff(), reason="r", duration_minutes=10
            ),
        }
    return org_a, owner_a, org_b, objects


DETAIL_ROUTES = ["branches", "departments", "memberships", "roles", "invitations", "support-grants"]


@pytest.mark.parametrize("route", DETAIL_ROUTES)
def test_reading_other_tenants_object_by_id_is_404(two_orgs, client_for, route):
    org_a, owner_a, _org_b, objects = two_orgs
    response = client_for(owner_a, org_a).get(f"/api/v1/{route}/{objects[route].id}/")
    assert response.status_code == 404


@pytest.mark.parametrize(
    ("route", "method", "suffix", "body"),
    [
        ("branches", "patch", "", {"name": "pwned"}),
        ("departments", "patch", "", {"name": "pwned"}),
        ("departments", "delete", "", None),
        ("memberships", "post", "suspend/", None),
        ("memberships", "delete", "", None),
        ("roles", "patch", "", {"name": "pwned"}),
        ("roles", "delete", "", None),
        ("role-assignments", "delete", "", None),
        ("invitations", "post", "revoke/", None),
        ("support-grants", "post", "approve/", None),
    ],
)
def test_changing_other_tenants_object_by_id_is_404(
    two_orgs, client_for, route, method, suffix, body
):
    org_a, owner_a, _org_b, objects = two_orgs
    client = client_for(owner_a, org_a)
    response = getattr(client, method)(
        f"/api/v1/{route}/{objects[route].id}/{suffix}", body, format="json"
    )
    assert response.status_code == 404


LIST_ROUTES = [
    "branches",
    "departments",
    "memberships",
    "roles",
    "role-assignments",
    "invitations",
    "support-grants",
    "audit-events",
]


@pytest.mark.parametrize("route", LIST_ROUTES)
def test_lists_never_contain_other_tenants_rows(two_orgs, client_for, route):
    org_a, owner_a, _org_b, objects = two_orgs
    body = client_for(owner_a, org_a).get(f"/api/v1/{route}/").json()
    ids = {str(row["id"]) for row in body["results"]}
    if route in objects:
        assert str(objects[route].id) not in ids


@pytest.mark.parametrize("route", [*LIST_ROUTES, "me/access", "organisations/current"])
def test_sending_another_tenants_header_is_404(two_orgs, client_for, route):
    _org_a, owner_a, org_b, _objects = two_orgs
    assert client_for(owner_a, org_b).get(f"/api/v1/{route}/").status_code == 404


def test_cannot_reference_other_tenants_objects_in_writes(two_orgs, client_for):
    org_a, owner_a, _org_b, objects = two_orgs
    client = client_for(owner_a, org_a)
    dept = client.post(
        "/api/v1/departments/", {"name": "X", "branch": str(objects["branches"].id)}, format="json"
    )
    assert dept.status_code == 400 and dept.json()["errors"][0]["field"] == "branch"
    assign = client.post(
        "/api/v1/role-assignments/",
        {"membership": str(objects["memberships"].id), "template_key": "staff"},
        format="json",
    )
    assert assign.status_code == 400
    custom_role = client.post(
        "/api/v1/role-assignments/",
        {"membership": str(objects["memberships"].id), "role": str(objects["roles"].id)},
        format="json",
    )
    assert custom_role.status_code == 400


# ------------------------------------------------------------------ declarations
def _tenant_endpoints(
    resolver: URLResolver | None = None, prefix: str = ""
) -> list[tuple[str, object]]:
    resolver = resolver or get_resolver()
    found = []
    for pattern in resolver.url_patterns:
        if isinstance(pattern, URLResolver):
            found += _tenant_endpoints(pattern, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            cls = getattr(pattern.callback, "cls", None)
            if cls and issubclass(cls, TenantAPIMixin):
                found.append((prefix + str(pattern.pattern), pattern.callback))
    return found


def test_tenant_endpoints_exist():
    assert len(_tenant_endpoints()) >= 10


@pytest.mark.parametrize("path_callback", _tenant_endpoints(), ids=lambda pc: pc[0])
def test_every_tenant_action_declares_a_permission(path_callback):
    """No tenant endpoint can be reached without an explicit permission decision."""
    _path, callback = path_callback
    cls = callback.cls
    actions = getattr(callback, "actions", None)
    if actions:  # viewset: http method -> action name
        keys = set(actions.values())
    else:
        keys = {
            m for m in cls.http_method_names if hasattr(cls, m) and m not in ("options", "head")
        }
    missing = keys - set(cls.required_permissions)
    assert missing == set(), f"{cls.__name__} lacks permissions for {missing}"


def test_unsupported_methods_are_405_not_500(new_org, client_for):
    org, owner = new_org()
    with tenant_context(org.id):
        role = Role.objects.create(name="R", permissions=[])
    client = client_for(owner, org)
    assert client.put(f"/api/v1/roles/{role.id}/", {}, format="json").status_code == 405
    assert client.delete("/api/v1/branches/").status_code == 405
