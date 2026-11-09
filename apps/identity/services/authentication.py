"""Sign-in: password check, brute-force protection and the MFA challenge step."""

from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import authenticate
from django.db import transaction
from django.http import HttpRequest
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from common.exceptions import BusinessRuleViolation, RateLimitedError

from .. import login_protection
from ..models import OneTimeToken, User, UserManager
from ..tokens import hash_token, new_opaque_token
from .mfa import InvalidMFACode, confirmed_device, verify_second_factor
from .sessions import ClientInfo, IssuedTokens, start_session

MFA_CHALLENGE_TTL = timedelta(minutes=5)
MFA_MAX_ATTEMPTS = 5


class InvalidCredentials(BusinessRuleViolation):
    default_code = "invalid_credentials"
    title = _("Incorrect email or password.")


class InvalidMFAChallenge(BusinessRuleViolation):
    default_code = "invalid_mfa_token"
    title = _("The sign-in attempt has expired. Sign in again.")


@dataclass(frozen=True)
class LoginResult:
    tokens: IssuedTokens | None = None
    mfa_token: str | None = None


def login(
    *, email: str, password: str, client: ClientInfo, request: HttpRequest | None = None
) -> LoginResult:
    email = UserManager.normalize_email(email)
    wait = login_protection.seconds_until_allowed(email)
    if wait:
        raise RateLimitedError(
            _("Too many failed sign-in attempts. Try again later."),
            retry_after=wait,
            code="login_temporarily_blocked",
        )

    # Django's ModelBackend: constant-time-ish (hashes even for unknown users) and
    # refuses inactive accounts.
    user = authenticate(request, username=email, password=password)
    if user is None:
        login_protection.record_failure(email)
        record(
            "auth.login.failed",
            metadata={"reason": "invalid_credentials"},
            ip_address=client.ip_address,
        )
        raise InvalidCredentials(_("Incorrect email or password."))
    if not isinstance(user, User):  # pragma: no cover - only our user model exists
        raise InvalidCredentials(_("Incorrect email or password."))
    login_protection.clear(email)

    if confirmed_device(user) is not None:
        raw, token_hash = new_opaque_token()
        OneTimeToken.objects.create(
            user=user,
            purpose=OneTimeToken.Purpose.MFA_CHALLENGE,
            token_hash=token_hash,
            expires_at=timezone.now() + MFA_CHALLENGE_TTL,
            data={"attempts": 0},
        )
        record(
            "auth.login.mfa_challenged",
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=client.ip_address,
        )
        return LoginResult(mfa_token=raw)

    tokens = start_session(user, amr=["pwd"], client=client)
    return LoginResult(tokens=tokens)


def complete_mfa_login(
    *, mfa_token: str, code: str = "", recovery_code: str = "", client: ClientInfo
) -> IssuedTokens:
    failure: InvalidMFACode | None = None
    with transaction.atomic():
        challenge = (
            OneTimeToken.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=hash_token(mfa_token), purpose=OneTimeToken.Purpose.MFA_CHALLENGE)
            .first()
        )
        if challenge is None or not challenge.is_valid() or not challenge.user.is_active:
            raise InvalidMFAChallenge(_("The sign-in attempt has expired. Sign in again."))
        user = challenge.user
        try:
            method = verify_second_factor(user, code=code, recovery_code=recovery_code)
        except InvalidMFACode as exc:
            attempts = int(challenge.data.get("attempts", 0)) + 1
            challenge.data = {**challenge.data, "attempts": attempts}
            if attempts >= MFA_MAX_ATTEMPTS:
                challenge.used_at = timezone.now()
            challenge.save(update_fields=["data", "used_at"])
            record(
                "auth.login.mfa_failed",
                actor_id=user.id,
                resource_type="user",
                resource_id=user.id,
                ip_address=client.ip_address,
            )
            failure = exc
        else:
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            amr = ["pwd", "otp"] if method == "otp" else ["pwd", "otp", "recovery"]
            return start_session(user, amr=amr, client=client)
    # Raised after commit so the attempt counter is kept.
    raise failure
