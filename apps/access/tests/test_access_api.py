"""Organisations, memberships, roles, invitations, MFA policy, support access, audit."""

import re

import pytest
from django.core import mail
from django.db import connection, transaction

from apps.access.models import Membership, Role, RoleAssignment
from apps.audit.models import AuditEvent
from apps.identity.models import User
from apps.organisations.models import Branch, OrganisationSettings
from common.tenancy.context import tenant_context
from conftest import ORG_DATA, PASSWORD

pytestmark = pytest.mark.django_db


def _branch(org, name="Main"):
    with tenant_context(org.id):
        return Branch.objects.create(name=name, country_code="UG", timezone="Africa/Kampala")


def _role(org, name, permissions):
    with tenant_context(org.id):
        return Role.objects.create(name=name, permissions=permissions)


def _token(message) -> str:
    match = re.search(r"#token=([\w\-.]+)", str(message.body))
    assert match is not None
    return match.group(1)


# ------------------------------------------------------------------ onboarding
def test_create_organisation_makes_creator_owner(make_user, client_for):
    user = make_user()
    response = client_for(user).post(
        "/api/v1/organisations/", {"name": "Kampala Clinic", **ORG_DATA}, format="json"
    )
    assert response.status_code == 201, response.json()
    org_id = response.json()["id"]

    memberships = client_for(user).get("/api/v1/me/memberships/").json()
    assert [m["organisation"]["id"] for m in memberships] == [org_id]

    access = client_for(user).get("/api/v1/me/access/", HTTP_X_ORGANISATION_ID=org_id).json()
    assert "access.role.assign" in access["permissions"]
    assert "organisations.settings.manage" in access["permissions"]  # sensitive, owner has it


def test_unverified_user_cannot_create_organisation(make_user, client_for):
    user = make_user(verified=False)
    response = client_for(user).post(
        "/api/v1/organisations/", {"name": "X", **ORG_DATA}, format="json"
    )
    assert response.status_code == 403 and response.json()["code"] == "email_not_verified"


def test_organisation_input_is_validated(make_user, client_for):
    client = client_for(make_user())
    unsupported = client.post(
        "/api/v1/organisations/", {"name": "X", "country_code": "ZZ"}, format="json"
    )
    assert unsupported.status_code == 400
    assert unsupported.json()["errors"][0]["code"] == "country_not_supported"
    bad_tz = client.post(
        "/api/v1/organisations/",
        {"name": "X", "country_code": "UG", "default_timezone": "Nope/Nope"},
        format="json",
    )
    assert bad_tz.status_code == 400
    assert {e["field"] for e in bad_tz.json()["errors"]} == {"default_timezone"}


def test_organisation_defaults_come_from_the_country(make_user, client_for):
    client = client_for(make_user())
    created = client.post(
        "/api/v1/organisations/", {"name": "Nairobi Shop", "country_code": "KE"}, format="json"
    )
    assert created.status_code == 201
    org = client.get(
        "/api/v1/organisations/current/", HTTP_X_ORGANISATION_ID=created.json()["id"]
    ).json()
    assert (org["base_currency"], org["default_timezone"], org["default_locale"]) == (
        "KES",
        "Africa/Nairobi",
        "en",
    )


def test_country_and_currency_cannot_be_changed(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    response = client.patch(
        "/api/v1/organisations/current/", {"name": "Renamed", "base_currency": "USD"}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed" and response.json()["base_currency"] == "UGX"


# ------------------------------------------------------------------ tenant resolution
def test_tenant_endpoints_need_the_organisation_header(new_org, client_for):
    _org, owner = new_org()
    response = client_for(owner).get("/api/v1/branches/")
    assert response.status_code == 400 and response.json()["code"] == "organisation_required"


@pytest.mark.parametrize("header", ["not-a-uuid", "01900000-0000-7000-8000-000000000000"])
def test_unknown_organisation_is_404(new_org, client_for, header):
    _org, owner = new_org()
    response = client_for(owner).get("/api/v1/branches/", HTTP_X_ORGANISATION_ID=header)
    assert response.status_code == 404


def test_suspended_member_loses_access(new_org, add_member, client_for):
    org, owner = new_org()
    user, membership = add_member(org, "staff@example.com")
    assert client_for(user, org).get("/api/v1/branches/").status_code == 200
    assert (
        client_for(owner, org).post(f"/api/v1/memberships/{membership.id}/suspend/").status_code
        == 200
    )
    assert client_for(user, org).get("/api/v1/branches/").status_code == 404


def test_unauthenticated_tenant_request_is_401(new_org, api):
    org, _owner = new_org()
    response = api.get("/api/v1/branches/", HTTP_X_ORGANISATION_ID=str(org.id))
    assert response.status_code == 401


# ------------------------------------------------------------------ permissions and escalation
def test_staff_cannot_manage_but_can_view(new_org, add_member, client_for):
    org, _owner = new_org()
    user, _ = add_member(org, "staff@example.com", "staff")
    client = client_for(user, org)
    assert client.get("/api/v1/branches/").status_code == 200
    denied = client.post("/api/v1/branches/", {"name": "New"}, format="json")
    assert denied.status_code == 403 and denied.json()["code"] == "permission_denied"
    assert client.get("/api/v1/memberships/").status_code == 403


def test_administrator_cannot_grant_owner_or_sensitive_permissions(new_org, add_member, client_for):
    org, _owner = new_org()
    admin, _ = add_member(org, "admin@example.com", "administrator")
    _target, target_membership = add_member(org, "t@example.com", "staff")
    client = client_for(admin, org)

    owner_grant = client.post(
        "/api/v1/role-assignments/",
        {"membership": str(target_membership.id), "template_key": "owner"},
        format="json",
    )
    assert owner_grant.status_code == 403 and owner_grant.json()["code"] == "cannot_grant"

    sneaky_role = client.post(
        "/api/v1/roles/", {"name": "Auditor", "permissions": ["audit.event.view"]}, format="json"
    )
    assert sneaky_role.status_code == 403 and sneaky_role.json()["code"] == "cannot_grant"

    fine = client.post(
        "/api/v1/role-assignments/",
        {"membership": str(target_membership.id), "template_key": "manager"},
        format="json",
    )
    assert fine.status_code == 201


def test_cannot_manage_a_more_powerful_member(new_org, add_member, client_for):
    org, owner = new_org()
    admin, _ = add_member(org, "admin@example.com", "administrator")
    with tenant_context(org.id):
        owner_membership = Membership.objects.get(user=owner)
    response = client_for(admin, org).post(f"/api/v1/memberships/{owner_membership.id}/suspend/")
    assert response.status_code == 403 and response.json()["code"] == "cannot_manage_member"


def test_cannot_manage_yourself_through_admin_endpoints(new_org, client_for):
    org, owner = new_org()
    with tenant_context(org.id):
        mine = Membership.objects.get(user=owner)
    response = client_for(owner, org).post(f"/api/v1/memberships/{mine.id}/suspend/")
    assert response.status_code == 403 and response.json()["code"] == "cannot_manage_self"


def test_last_owner_is_protected(new_org, add_member, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    leave = client.post("/api/v1/memberships/leave/")
    assert leave.status_code == 409 and leave.json()["code"] == "last_owner"
    with tenant_context(org.id):
        own_owner_role = RoleAssignment.objects.get(membership__user=owner, template_key="owner")
    drop = client.delete(f"/api/v1/role-assignments/{own_owner_role.id}/")
    assert drop.status_code == 409

    # With a second owner, the first can leave.
    add_member(org, "second@example.com", "owner")
    assert client.post("/api/v1/memberships/leave/").status_code == 204


def test_editing_a_role_requires_holding_its_permissions(new_org, add_member, client_for):
    org, _owner = new_org()
    admin, _ = add_member(org, "admin@example.com", "administrator")
    role = _role(org, "Finance", ["audit.event.view"])
    response = client_for(admin, org).patch(
        f"/api/v1/roles/{role.id}/", {"name": "Renamed"}, format="json"
    )
    assert response.status_code == 403


def test_role_in_use_cannot_be_deleted(new_org, add_member, client_for):
    org, owner = new_org()
    role = _role(org, "Cashier", ["organisations.branch.view"])
    add_member(org, "c@example.com", None, role=role)
    response = client_for(owner, org).delete(f"/api/v1/roles/{role.id}/")
    assert response.status_code == 409 and response.json()["code"] == "role_in_use"


def test_unknown_permission_in_custom_role_is_rejected(new_org, client_for):
    org, owner = new_org()
    response = client_for(owner, org).post(
        "/api/v1/roles/", {"name": "X", "permissions": ["made.up.permission"]}, format="json"
    )
    assert (
        response.status_code == 400 and response.json()["errors"][0]["code"] == "unknown_permission"
    )


def test_duplicate_role_name_is_a_validation_error_not_a_crash(new_org, client_for):
    org, owner = new_org()
    client = client_for(owner, org)
    client.post("/api/v1/roles/", {"name": "Nurse", "permissions": []}, format="json")
    response = client.post("/api/v1/roles/", {"name": "Nurse", "permissions": []}, format="json")
    assert response.status_code == 400


def test_sensitive_grant_emails_owners(
    new_org, add_member, client_for, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    _user, membership = add_member(org, "fin@example.com", "staff")
    role = _role(org, "Auditor", ["audit.event.view"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for(owner, org).post(
            "/api/v1/role-assignments/",
            {"membership": str(membership.id), "role": str(role.id)},
            format="json",
        )
    assert response.status_code == 201
    assert any(owner.email in m.to and "Sensitive" in m.subject for m in mail.outbox)


# ------------------------------------------------------------------ branch scoping
def test_branch_scoped_role_sees_and_manages_only_its_branch(new_org, add_member, client_for):
    org, _owner = new_org()
    north, south = _branch(org, "North"), _branch(org, "South")
    role = _role(org, "Branch lead", ["organisations.branch.view", "organisations.branch.manage"])
    lead, _ = add_member(org, "lead@example.com", None, role=role, branch=north)
    client = client_for(lead, org)

    names = [b["name"] for b in client.get("/api/v1/branches/").json()["results"]]
    assert names == ["North"]
    assert client.get(f"/api/v1/branches/{south.id}/").status_code == 404
    assert (
        client.patch(f"/api/v1/branches/{north.id}/", {"region": "Gulu"}, format="json").status_code
        == 200
    )
    # Creating a branch is organisation-wide: the branch-scoped grant is not enough.
    denied = client.post("/api/v1/branches/", {"name": "East"}, format="json")
    assert denied.status_code == 403 and denied.json()["code"] == "branch_not_permitted"


def test_departments_follow_branch_scope(new_org, add_member, client_for):
    org, _owner = new_org()
    north = _branch(org, "North")
    role = _role(
        org, "Branch lead", ["organisations.department.view", "organisations.department.manage"]
    )
    lead, _ = add_member(org, "lead@example.com", None, role=role, branch=north)
    client = client_for(lead, org)
    ok = client.post(
        "/api/v1/departments/", {"name": "Lab", "branch": str(north.id)}, format="json"
    )
    assert ok.status_code == 201
    org_level = client.post("/api/v1/departments/", {"name": "Finance"}, format="json")
    assert org_level.status_code == 403


# ------------------------------------------------------------------ MFA policy
def test_owner_must_use_mfa_before_requiring_it(new_org, client_for):
    org, owner = new_org()
    denied = client_for(owner, org).patch(
        "/api/v1/organisations/current/settings/", {"require_mfa": True}, format="json"
    )
    assert denied.status_code == 403 and denied.json()["code"] == "mfa_required_to_enforce"
    ok = client_for(owner, org, mfa=True).patch(
        "/api/v1/organisations/current/settings/", {"require_mfa": True}, format="json"
    )
    assert ok.status_code == 200


def test_required_mfa_blocks_members_without_it(new_org, add_member, client_for):
    org, _owner = new_org()
    with tenant_context(org.id):
        OrganisationSettings.objects.update(require_mfa=True)
    user, _ = add_member(org, "staff@example.com")
    blocked = client_for(user, org).get("/api/v1/branches/")
    assert blocked.status_code == 403 and blocked.json()["code"] == "mfa_required"
    assert client_for(user, org, mfa=True).get("/api/v1/branches/").status_code == 200


# ------------------------------------------------------------------ invitations
def test_invite_new_person_who_creates_an_account(
    new_org, client_for, api, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    with django_capture_on_commit_callbacks(execute=True):
        invited = client_for(owner, org).post(
            "/api/v1/invitations/",
            {"email": "Nurse@Example.com", "template_key": "staff"},
            format="json",
        )
    assert invited.status_code == 201
    token = _token(mail.outbox[-1])
    assert token.startswith(str(org.id))

    preview = api.post("/api/v1/invitations/preview/", {"token": token}, format="json").json()
    assert preview["organisation_name"] == org.name and preview["role_name"] == "Staff member"

    accepted = api.post(
        "/api/v1/invitations/accept-new/",
        {"token": token, "full_name": "Nurse Joy", "password": PASSWORD},
        format="json",
    )
    assert accepted.status_code == 201
    user = User.objects.get(email="nurse@example.com")
    assert user.email_verified_at is not None
    client = APIClient_with(accepted.json()["access_token"], org)
    assert client.get("/api/v1/branches/").status_code == 200
    # Single use.
    assert (
        api.post("/api/v1/invitations/preview/", {"token": token}, format="json").status_code == 400
    )


def APIClient_with(access_token, org):  # noqa: N802 - small local helper
    from rest_framework.test import APIClient

    client = APIClient()
    client.credentials(
        HTTP_AUTHORIZATION=f"Bearer {access_token}", HTTP_X_ORGANISATION_ID=str(org.id)
    )
    return client


def test_existing_user_accepts_but_only_for_their_own_email(
    new_org, make_user, client_for, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    invitee = make_user("doc@example.com")
    stranger = make_user("stranger@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        client_for(owner, org).post(
            "/api/v1/invitations/",
            {"email": invitee.email, "template_key": "manager"},
            format="json",
        )
    token = _token(mail.outbox[-1])
    wrong = client_for(stranger).post(
        "/api/v1/invitations/accept/", {"token": token}, format="json"
    )
    assert wrong.status_code == 403 and wrong.json()["code"] == "invitation_email_mismatch"
    ok = client_for(invitee).post("/api/v1/invitations/accept/", {"token": token}, format="json")
    assert ok.status_code == 200
    assert (
        client_for(invitee, org).get("/api/v1/memberships/").status_code == 200
    )  # manager can view


def test_revoked_or_tampered_invitations_do_not_work(
    new_org, client_for, api, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    client = client_for(owner, org)
    with django_capture_on_commit_callbacks(execute=True):
        invitation = client.post(
            "/api/v1/invitations/",
            {"email": "a@example.com", "template_key": "staff"},
            format="json",
        ).json()
    token = _token(mail.outbox[-1])
    tampered = token[:-4] + "AAAA"
    assert (
        api.post("/api/v1/invitations/preview/", {"token": tampered}, format="json").status_code
        == 400
    )
    assert client.post(f"/api/v1/invitations/{invitation['id']}/revoke/").status_code == 200
    assert (
        api.post("/api/v1/invitations/preview/", {"token": token}, format="json").status_code == 400
    )


def test_cannot_invite_with_more_power_than_you_have(new_org, add_member, client_for):
    org, _owner = new_org()
    admin, _ = add_member(org, "admin@example.com", "administrator")
    response = client_for(admin, org).post(
        "/api/v1/invitations/", {"email": "x@example.com", "template_key": "owner"}, format="json"
    )
    assert response.status_code == 403


# ------------------------------------------------------------------ support access
def test_support_access_lifecycle(
    new_org, make_staff, client_for, django_capture_on_commit_callbacks
):
    org, owner = new_org()
    staff = make_staff()
    staff_client = client_for(staff, mfa=True)

    # No grant: the organisation is invisible.
    assert client_for(staff, org, mfa=True).get("/api/v1/branches/").status_code == 404

    with django_capture_on_commit_callbacks(execute=True):
        requested = staff_client.post(
            "/api/v1/platform/support-grants/",
            {
                "organisation_id": str(org.id),
                "reason": "Ticket 42: missing branch",
                "duration_minutes": 60,
            },
            format="json",
        )
    assert requested.status_code == 201
    assert any(owner.email in m.to for m in mail.outbox)
    assert (
        client_for(staff, org, mfa=True).get("/api/v1/branches/").status_code == 404
    )  # not approved yet

    grant_id = requested.json()["id"]
    assert (
        client_for(owner, org).post(f"/api/v1/support-grants/{grant_id}/approve/").status_code
        == 200
    )

    support = client_for(staff, org, mfa=True)
    assert support.get("/api/v1/branches/").status_code == 200
    write = support.post("/api/v1/branches/", {"name": "X"}, format="json")
    assert write.status_code == 403 and write.json()["code"] == "support_read_only"
    assert support.get("/api/v1/audit-events/").status_code == 403  # sensitive: never for support
    assert client_for(staff, org).get("/api/v1/branches/").status_code == 403  # needs MFA
    with tenant_context(org.id):
        assert AuditEvent.objects.filter(action="support.request", organisation_id=org.id).exists()

    assert (
        client_for(owner, org).post(f"/api/v1/support-grants/{grant_id}/revoke/").status_code == 200
    )
    assert support.get("/api/v1/branches/").status_code == 404


def test_platform_endpoints_need_staff_with_mfa(new_org, make_staff, make_user, client_for):
    new_org()
    assert (
        client_for(make_user("x@example.com"), mfa=True)
        .get("/api/v1/platform/organisations/")
        .status_code
        == 403
    )
    staff = make_staff()
    assert client_for(staff).get("/api/v1/platform/organisations/").status_code == 403
    assert client_for(staff, mfa=True).get("/api/v1/platform/organisations/").json()["count"] == 1


def test_only_super_admin_can_break_glass(new_org, make_staff, client_for):
    org, _owner = new_org()
    support = make_staff("s@tillfree.test", "support_admin")
    payload = {
        "organisation_id": str(org.id),
        "reason": "Outage",
        "duration_minutes": 30,
        "break_glass": True,
    }
    denied = client_for(support, mfa=True).post(
        "/api/v1/platform/support-grants/", payload, format="json"
    )
    assert denied.status_code == 403
    root = make_staff("root@tillfree.test", "super_admin")
    opened = client_for(root, mfa=True).post(
        "/api/v1/platform/support-grants/", payload, format="json"
    )
    assert opened.status_code == 201 and opened.json()["status"] == "approved"


# ------------------------------------------------------------------ audit log
def test_audit_log_is_readable_by_owner_only_and_append_only(new_org, add_member, client_for):
    org, owner = new_org()
    staff, _ = add_member(org, "staff@example.com")
    events = client_for(owner, org).get("/api/v1/audit-events/").json()["results"]
    assert "organisation.created" in {e["action"] for e in events}
    assert client_for(staff, org).get("/api/v1/audit-events/").status_code == 403

    with tenant_context(org.id):
        assert AuditEvent.objects.filter(organisation_id=org.id).update(action="tampered") == 0
        assert AuditEvent.objects.filter(organisation_id=org.id).delete()[0] == 0
        with connection.cursor() as cursor, transaction.atomic():
            cursor.execute("UPDATE audit_auditevent SET action = 'x'")
            assert cursor.rowcount == 0


def test_audit_log_never_shows_other_organisations(new_org, client_for):
    org_a, owner_a = new_org("A", "a@example.com")
    org_b, _owner_b = new_org("B", "b@example.com")
    events = client_for(owner_a, org_a).get("/api/v1/audit-events/").json()["results"]
    assert events and all(e["resource_id"] != str(org_b.id) for e in events)


def test_my_access_reports_grants_for_the_ui(new_org, add_member, client_for):
    org, _owner = new_org()
    north = _branch(org)
    role = _role(org, "Lead", ["organisations.branch.view"])
    user, _ = add_member(org, "lead@example.com", None, role=role, branch=north)
    body = client_for(user, org).get("/api/v1/me/access/").json()
    assert body["grants"] == [
        {"branch_id": str(north.id), "permissions": ["organisations.branch.view"]}
    ]
    assert body["support_access"] is False
