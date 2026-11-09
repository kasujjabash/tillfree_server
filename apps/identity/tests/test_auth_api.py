"""Authentication API: registration, sign-in, tokens, sessions, passwords, MFA, email change."""

import re
from datetime import timedelta

import pyotp
import pytest
from django.core import mail
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from apps.audit.models import AuditEvent
from apps.identity import login_protection
from apps.identity.models import AuthSession, OneTimeToken, User
from apps.identity.tokens import issue_access_token
from common.tenancy.context import user_context
from conftest import PASSWORD, totp_now

pytestmark = pytest.mark.django_db

LOGIN = "/api/v1/auth/login/"
REFRESH = "/api/v1/auth/token/refresh/"
ME = "/api/v1/me/"


def _token_from_email(message: mail.EmailMessage) -> str:
    match = re.search(r"#token=([\w\-.]+)", str(message.body))
    assert match, message.body
    return match.group(1)


def _login(api, email="user@example.com", password=PASSWORD, **extra):
    return api.post(LOGIN, {"email": email, "password": password, **extra}, format="json")


def _bearer(api, token):
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return api


# ------------------------------------------------------------------ registration
def test_register_then_verify_email(api, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/auth/register/",
            {"email": "New@Example.com", "password": PASSWORD, "full_name": "Amina N"},
            format="json",
        )
    assert response.status_code == 202
    user = User.objects.get(email="new@example.com")
    assert user.email_verified_at is None
    assert len(mail.outbox) == 1 and "Verify" in mail.outbox[0].subject

    token = _token_from_email(mail.outbox[0])
    assert (
        api.post("/api/v1/auth/verify-email/", {"token": token}, format="json").status_code == 200
    )
    user.refresh_from_db()
    assert user.email_verified_at is not None
    # Single use.
    again = api.post("/api/v1/auth/verify-email/", {"token": token}, format="json")
    assert again.status_code == 400 and again.json()["code"] == "invalid_token"


def test_register_existing_email_gives_same_response_and_tells_the_owner(
    api, make_user, django_capture_on_commit_callbacks
):
    make_user("taken@example.com")
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/auth/register/",
            {"email": "TAKEN@example.com", "password": PASSWORD},
            format="json",
        )
    assert response.status_code == 202  # same as a new address: no account enumeration
    assert User.objects.filter(email="taken@example.com").count() == 1
    assert "already" in mail.outbox[0].subject.lower() or "tried" in mail.outbox[0].subject.lower()


def test_register_rejects_weak_password(api):
    response = api.post(
        "/api/v1/auth/register/", {"email": "w@example.com", "password": "password"}, format="json"
    )
    assert response.status_code == 400
    assert response.json()["code"] == "validation_error"
    assert not User.objects.filter(email="w@example.com").exists()


# ------------------------------------------------------------------ sign-in and tokens
def test_login_returns_tokens_that_authenticate(api, make_user):
    make_user()
    body = _login(api).json()
    assert set(body) >= {"access_token", "refresh_token", "session_id", "access_token_expires_at"}
    me = _bearer(api, body["access_token"]).get(ME)
    assert me.status_code == 200 and me.json()["email"] == "user@example.com"
    user = User.objects.get(email="user@example.com")
    with transaction.atomic(), user_context(user.id):
        assert AuditEvent.objects.filter(action="auth.session.started", actor_id=user.id).exists()
    # Without the user's context the account event is invisible.
    with transaction.atomic():
        assert not AuditEvent.objects.filter(action="auth.session.started").exists()


def test_wrong_password_and_unknown_email_look_the_same(api, make_user):
    make_user()
    wrong = _login(api, password="nope-nope-nope")
    unknown = _login(api, email="ghost@example.com")
    assert wrong.status_code == unknown.status_code == 400
    assert wrong.json()["code"] == unknown.json()["code"] == "invalid_credentials"


def test_inactive_user_cannot_log_in(api, make_user):
    make_user(is_active=False)
    assert _login(api).json()["code"] == "invalid_credentials"


def test_brute_force_triggers_progressive_delay_even_for_correct_password(api, make_user):
    make_user()
    for _ in range(login_protection.FAILURE_THRESHOLD):
        _login(api, password="wrong-password-1")
    blocked = _login(api)  # correct password, still blocked
    assert blocked.status_code == 429
    assert blocked.json()["code"] == "login_temporarily_blocked"
    assert int(blocked["Retry-After"]) > 0


def test_login_endpoint_is_rate_limited_per_ip(api, make_user):
    make_user()
    codes = [_login(api, email=f"x{i}@example.com").status_code for i in range(12)]
    assert 429 in codes


def test_refresh_rotates_and_reuse_revokes_the_session(api, make_user):
    make_user()
    first = _login(api).json()
    second = api.post(REFRESH, {"refresh_token": first["refresh_token"]}, format="json")
    assert second.status_code == 200
    assert second.json()["refresh_token"] != first["refresh_token"]

    # The old token is presented again: treated as stolen.
    reuse = api.post(REFRESH, {"refresh_token": first["refresh_token"]}, format="json")
    assert reuse.status_code == 401 and reuse.json()["code"] == "invalid_refresh_token"
    session = AuthSession.objects.get(pk=first["session_id"])
    assert session.revoked_reason == "refresh_token_reuse"
    # Every token of that session is now dead, including the newest ones.
    newest = second.json()
    assert (
        api.post(REFRESH, {"refresh_token": newest["refresh_token"]}, format="json").status_code
        == 401
    )
    assert _bearer(api, newest["access_token"]).get(ME).json()["code"] == "session_revoked"


def test_refresh_with_garbage_is_rejected(api):
    response = api.post(REFRESH, {"refresh_token": "not-a-token"}, format="json")
    assert response.status_code == 401


def test_logout_ends_the_session_immediately(api, make_user):
    make_user()
    tokens = _login(api).json()
    client = _bearer(api, tokens["access_token"])
    assert client.post("/api/v1/auth/logout/").status_code == 204
    response = client.get(ME)
    assert response.status_code == 401 and response.json()["code"] == "session_revoked"


def test_logout_all_ends_every_session(api, make_user):
    make_user()
    one, two = _login(api).json(), _login(api).json()
    _bearer(api, one["access_token"]).post("/api/v1/auth/logout-all/")
    assert _bearer(api, two["access_token"]).get(ME).status_code == 401


def test_expired_access_token_is_rejected(api, make_user):
    user = make_user()
    tokens = _login(api).json()
    session = AuthSession.objects.get(pk=tokens["session_id"])
    past = timezone.now() - timedelta(hours=1)
    expired, _ = issue_access_token(
        user_id=user.id, session_id=session.id, auth_time=past, amr=["pwd"], now=past
    )
    response = _bearer(api, expired).get(ME)
    assert response.status_code == 401 and response.json()["code"] == "token_expired"


@pytest.mark.parametrize(
    "token",
    ["garbage", "a.b.c", "eyJhbGciOiJub25lIn0.eyJzdWIiOiJ4In0."],  # includes alg=none
)
def test_malformed_or_unsigned_tokens_are_rejected(api, token):
    response = _bearer(api, token).get(ME)
    assert response.status_code == 401 and response.json()["code"] == "invalid_token"


def test_token_signed_with_another_key_is_rejected(api, make_user):
    make_user()
    tokens = _login(api).json()
    with override_settings(JWT_SIGNING_KEYS={"dev": "another-secret-that-is-long-enough-123"}):
        response = _bearer(api, tokens["access_token"]).get(ME)
    assert response.status_code == 401


def test_deactivating_a_user_cuts_off_their_tokens(api, make_user):
    user = make_user()
    tokens = _login(api).json()
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert _bearer(api, tokens["access_token"]).get(ME).json()["code"] == "account_disabled"


def test_web_clients_get_the_refresh_token_only_as_httponly_cookie(api, make_user):
    make_user()
    response = _login(api, use_cookie=True)
    assert "refresh_token" not in response.json()
    cookie = response.cookies["tf_refresh"]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert cookie["path"] == "/api/v1/auth/"
    refreshed = api.post(REFRESH, {}, format="json")  # cookie sent automatically
    assert refreshed.status_code == 200 and "refresh_token" not in refreshed.json()
    assert refreshed.cookies["tf_refresh"].value != cookie.value


# ------------------------------------------------------------------ sessions
def test_user_can_list_and_revoke_own_sessions_only(api, make_user):
    make_user()
    other_user = make_user("other@example.com")
    mine = _login(api).json()
    second = _login(api).json()
    theirs = _login(api, email=other_user.email).json()

    client = _bearer(api, mine["access_token"])
    listed = client.get("/api/v1/me/sessions/").json()
    assert {s["id"] for s in listed} == {mine["session_id"], second["session_id"]}
    assert [s["current"] for s in listed if s["id"] == mine["session_id"]] == [True]

    assert client.delete(f"/api/v1/me/sessions/{theirs['session_id']}/").status_code == 404
    assert client.delete(f"/api/v1/me/sessions/{second['session_id']}/").status_code == 204
    assert AuthSession.objects.get(pk=second["session_id"]).revoked_at is not None


# ------------------------------------------------------------------ profile
def test_profile_update_validates_and_resets_phone_verification(api, make_user):
    user = make_user(phone="+256712345678")
    User.objects.filter(pk=user.pk).update(phone_verified_at=timezone.now())
    client = _bearer(api, _login(api).json()["access_token"])
    bad = client.patch(ME, {"timezone": "Mars/Base"}, format="json")
    assert bad.status_code == 400
    ok = client.patch(ME, {"phone": "+254712345678", "full_name": "Amina"}, format="json")
    assert ok.status_code == 200 and ok.json()["phone_verified"] is False
    assert client.patch(ME, {"email": "x@example.com"}, format="json").json()["email"] == user.email


# ------------------------------------------------------------------ passwords
def test_forgot_and_reset_password(api, make_user, django_capture_on_commit_callbacks):
    make_user()
    old_session = _login(api).json()
    with django_capture_on_commit_callbacks(execute=True):
        assert (
            api.post(
                "/api/v1/auth/password/forgot/", {"email": "user@example.com"}, format="json"
            ).status_code
            == 202
        )
    token = _token_from_email(mail.outbox[-1])

    new_password = "Brand-New-Password-42"
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/auth/password/reset/",
            {"token": token, "new_password": new_password},
            format="json",
        )
    assert response.status_code == 200
    assert "changed" in mail.outbox[-1].subject.lower()
    assert _bearer(api, old_session["access_token"]).get(ME).status_code == 401  # signed out
    api.credentials()
    assert _login(api, password=new_password).status_code == 200
    reuse = api.post(
        "/api/v1/auth/password/reset/", {"token": token, "new_password": "X-9" * 5}, format="json"
    )
    assert reuse.status_code == 400


def test_forgot_password_for_unknown_email_sends_nothing(api, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/auth/password/forgot/", {"email": "ghost@example.com"}, format="json"
        )
    assert response.status_code == 202 and mail.outbox == []


def test_reset_token_expires(api, make_user, django_capture_on_commit_callbacks):
    make_user()
    with django_capture_on_commit_callbacks(execute=True):
        api.post("/api/v1/auth/password/forgot/", {"email": "user@example.com"}, format="json")
    token = _token_from_email(mail.outbox[-1])
    OneTimeToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = api.post(
        "/api/v1/auth/password/reset/",
        {"token": token, "new_password": "Another-Pass-77"},
        format="json",
    )
    assert response.status_code == 400 and response.json()["code"] == "invalid_token"


def test_change_password_keeps_current_session_only(api, make_user):
    make_user()
    current = _login(api).json()
    other = _login(api).json()
    client = _bearer(api, current["access_token"])
    wrong = client.post(
        "/api/v1/auth/password/change/",
        {"current_password": "wrong", "new_password": "Another-Pass-77"},
        format="json",
    )
    assert wrong.status_code == 400 and wrong.json()["errors"][0]["field"] == "current_password"
    ok = client.post(
        "/api/v1/auth/password/change/",
        {"current_password": PASSWORD, "new_password": "Another-Pass-77"},
        format="json",
    )
    assert ok.status_code == 200
    assert client.get(ME).status_code == 200
    assert _bearer(api, other["access_token"]).get(ME).status_code == 401


# ------------------------------------------------------------------ MFA
def _enable_mfa(client) -> tuple[str, list[str]]:
    setup = client.post("/api/v1/auth/mfa/totp/setup/").json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/Tillfree")
    confirm = client.post(
        "/api/v1/auth/mfa/totp/confirm/", {"code": totp_now(setup["secret"])}, format="json"
    )
    assert confirm.status_code == 200, confirm.json()
    return setup["secret"], confirm.json()["recovery_codes"]


def test_mfa_enrolment_and_two_step_login(api, make_user):
    make_user()
    client = _bearer(api, _login(api).json()["access_token"])
    secret, codes = _enable_mfa(client)
    assert len(codes) == 10
    assert client.get(ME).json()["mfa_enabled"] is True

    api.credentials()
    first = _login(api).json()
    assert first == {"mfa_required": True, "mfa_token": first["mfa_token"]}
    # The code from enrolment was already used in this time step (replay protection),
    # so use the next step's code, which the drift window accepts.
    next_code = pyotp.TOTP(secret).at(timezone.now() + timedelta(seconds=30))
    verified = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": first["mfa_token"], "code": next_code},
        format="json",
    )
    assert verified.status_code == 200
    session = AuthSession.objects.get(pk=verified.json()["session_id"])
    assert "otp" in session.amr

    # Replaying the same code is refused.
    second = _login(api).json()
    replay = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": second["mfa_token"], "code": next_code},
        format="json",
    )
    assert replay.status_code == 400 and replay.json()["code"] == "invalid_mfa_code"


def test_recovery_code_works_exactly_once(api, make_user):
    make_user()
    client = _bearer(api, _login(api).json()["access_token"])
    _secret, codes = _enable_mfa(client)
    api.credentials()
    challenge = _login(api).json()["mfa_token"]
    ok = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge, "recovery_code": codes[0].upper()},
        format="json",
    )
    assert ok.status_code == 200
    challenge = _login(api).json()["mfa_token"]
    again = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge, "recovery_code": codes[0]},
        format="json",
    )
    assert again.status_code == 400


def test_mfa_challenge_dies_after_too_many_wrong_codes(api, make_user, totp_secret):
    make_user(mfa_secret=totp_secret)
    challenge = _login(api).json()["mfa_token"]
    for _ in range(5):
        api.post(
            "/api/v1/auth/mfa/verify/", {"mfa_token": challenge, "code": "000000"}, format="json"
        )
    response = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge, "code": totp_now(totp_secret)},
        format="json",
    )
    assert response.status_code == 400 and response.json()["code"] == "invalid_mfa_token"


def test_disabling_mfa_needs_password_and_code(api, make_user, totp_secret):
    make_user(mfa_secret=totp_secret)
    challenge = _login(api).json()["mfa_token"]
    tokens = api.post(
        "/api/v1/auth/mfa/verify/",
        {"mfa_token": challenge, "code": totp_now(totp_secret)},
        format="json",
    ).json()
    client = _bearer(api, tokens["access_token"])
    bad = client.post(
        "/api/v1/auth/mfa/totp/disable/", {"password": "wrong", "code": "123456"}, format="json"
    )
    assert bad.status_code == 400
    next_code = pyotp.TOTP(totp_secret).at(timezone.now() + timedelta(seconds=30))
    ok = client.post(
        "/api/v1/auth/mfa/totp/disable/", {"password": PASSWORD, "code": next_code}, format="json"
    )
    assert ok.status_code == 200
    assert client.get(ME).json()["mfa_enabled"] is False


def test_totp_secret_is_encrypted_at_rest(api, make_user):
    make_user()
    client = _bearer(api, _login(api).json()["access_token"])
    secret = client.post("/api/v1/auth/mfa/totp/setup/").json()["secret"]
    from apps.identity.models import TOTPDevice

    assert secret not in TOTPDevice.objects.get().encrypted_secret


# ------------------------------------------------------------------ email change
def test_email_change_requires_confirmation_from_new_address(
    api, make_user, django_capture_on_commit_callbacks
):
    make_user()
    client = _bearer(api, _login(api).json()["access_token"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/api/v1/auth/email/change/",
            {"new_email": "new@example.com", "password": PASSWORD},
            format="json",
        )
    assert response.status_code == 202
    to_new = next(m for m in mail.outbox if m.to == ["new@example.com"])
    assert any(m.to == ["user@example.com"] for m in mail.outbox)  # old address warned
    assert client.get(ME).json()["email"] == "user@example.com"  # not changed yet

    api.credentials()
    confirmed = api.post(
        "/api/v1/auth/email/confirm/", {"token": _token_from_email(to_new)}, format="json"
    )
    assert confirmed.status_code == 200
    assert User.objects.filter(email="new@example.com").exists()
    assert _login(api, email="new@example.com").status_code == 200


def test_email_change_to_taken_address_reveals_nothing(
    api, make_user, django_capture_on_commit_callbacks
):
    make_user()
    make_user("taken@example.com")
    client = _bearer(api, _login(api).json()["access_token"])
    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            "/api/v1/auth/email/change/",
            {"new_email": "taken@example.com", "password": PASSWORD},
            format="json",
        )
    assert response.status_code == 202 and mail.outbox == []


def test_queue_outage_does_not_fail_an_already_committed_request(
    api, monkeypatch, django_capture_on_commit_callbacks
):
    """Email is queued after commit with robust=True: a broker outage is logged, not raised."""
    from common import tasks

    def broker_down(*args, **kwargs):
        raise ConnectionError("broker unreachable")

    monkeypatch.setattr(tasks.send_email, "delay", broker_down)
    with django_capture_on_commit_callbacks(execute=True):
        response = api.post(
            "/api/v1/auth/register/",
            {"email": "q@example.com", "password": PASSWORD},
            format="json",
        )
    assert response.status_code == 202
    assert User.objects.filter(email="q@example.com").exists()
