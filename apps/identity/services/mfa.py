"""Authenticator-app (TOTP, RFC 6238) multi-factor authentication and recovery codes."""

import hashlib
import hmac
import secrets
import time

import pyotp
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from common.exceptions import BusinessRuleViolation, ConflictError
from common.mail import queue_email

from ..crypto import decrypt, encrypt
from ..models import RecoveryCode, TOTPDevice, User
from .sessions import revoke_all_sessions

ISSUER = "Tillfree"
RECOVERY_CODE_COUNT = 10
VALID_WINDOW = 1  # accept the previous and next 30-second step for clock drift


class InvalidMFACode(BusinessRuleViolation):
    default_code = "invalid_mfa_code"
    title = _("The verification code is incorrect.")


def _normalise_recovery(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def _hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalise_recovery(code).encode()).hexdigest()


def _new_recovery_codes(user: User) -> list[str]:
    RecoveryCode.objects.filter(user=user).delete()
    codes = []
    for _i in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)  # 40 bits, shown as xxxxx-xxxxx
        codes.append(f"{raw[:5]}-{raw[5:]}")
    RecoveryCode.objects.bulk_create(
        [RecoveryCode(user=user, code_hash=_hash_recovery(c)) for c in codes]
    )
    return codes


def confirmed_device(user: User) -> TOTPDevice | None:
    return TOTPDevice.objects.filter(user=user, confirmed_at__isnull=False).first()


def verify_totp(device: TOTPDevice, code: str) -> bool:
    """Check a 6-digit code, rejecting any time step already used (replay protection).
    The caller must hold a row lock on ``device``."""
    code = code.strip().replace(" ", "")
    if not (code.isdigit() and len(code) == 6):
        return False
    totp = pyotp.TOTP(decrypt(device.encrypted_secret))
    current_step = int(time.time()) // totp.interval
    for step in range(current_step - VALID_WINDOW, current_step + VALID_WINDOW + 1):
        if device.last_used_step is not None and step <= device.last_used_step:
            continue
        if hmac.compare_digest(totp.generate_otp(step), code):
            device.last_used_step = step
            device.save(update_fields=["last_used_step", "updated_at"])
            return True
    return False


def use_recovery_code(user: User, code: str) -> bool:
    updated = RecoveryCode.objects.filter(
        user=user, code_hash=_hash_recovery(code), used_at__isnull=True
    ).update(used_at=timezone.now())
    return updated == 1


def verify_second_factor(user: User, *, code: str = "", recovery_code: str = "") -> str:
    """Verify a TOTP or recovery code for ``user``. Returns the method used ("otp" or
    "recovery"). Raises ``InvalidMFACode``."""
    device = (
        TOTPDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    )
    if device is None:
        raise InvalidMFACode(
            _("Multi-factor authentication is not enabled."), code="mfa_not_enabled"
        )
    if code and verify_totp(device, code):
        return "otp"
    if recovery_code and use_recovery_code(user, recovery_code):
        record(
            "auth.mfa.recovery_code_used",
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={
                "remaining": RecoveryCode.objects.filter(user=user, used_at__isnull=True).count()
            },
        )
        return "recovery"
    raise InvalidMFACode(_("The verification code is incorrect."))


@transaction.atomic
def begin_totp_setup(user: User) -> tuple[str, str]:
    """Create (or replace) an unconfirmed device. Returns (secret, otpauth URI)."""
    if confirmed_device(user) is not None:
        raise ConflictError(
            _("Multi-factor authentication is already enabled."), code="mfa_already_enabled"
        )
    secret = pyotp.random_base32()
    TOTPDevice.objects.update_or_create(
        user=user,
        defaults={
            "encrypted_secret": encrypt(secret),
            "confirmed_at": None,
            "last_used_step": None,
        },
    )
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=ISSUER)
    return secret, uri


@transaction.atomic
def confirm_totp_setup(user: User, code: str, *, ip_address: str | None = None) -> list[str]:
    """Activate the device with a first valid code. Returns recovery codes (shown once)."""
    device = TOTPDevice.objects.select_for_update().filter(user=user).first()
    if device is None:
        raise BusinessRuleViolation(_("Start the setup first."), code="mfa_setup_not_started")
    if device.confirmed_at is not None:
        raise ConflictError(
            _("Multi-factor authentication is already enabled."), code="mfa_already_enabled"
        )
    if not verify_totp(device, code):
        raise InvalidMFACode(_("The verification code is incorrect."), field="code")
    device.confirmed_at = timezone.now()
    device.save(update_fields=["confirmed_at", "updated_at"])
    codes = _new_recovery_codes(user)
    record(
        "auth.mfa.enabled",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    queue_email(to=user.email, template="identity/email/mfa_enabled", context={"user": user})
    return codes


@transaction.atomic
def disable_totp(
    user: User, *, password: str, code: str, keep_session_id: object, ip_address: str | None = None
) -> None:
    if not user.check_password(password):
        raise BusinessRuleViolation(
            _("The password is incorrect."), code="invalid_password", field="password"
        )
    verify_second_factor(user, code=code, recovery_code=code)
    TOTPDevice.objects.filter(user=user).delete()
    RecoveryCode.objects.filter(user=user).delete()
    revoke_all_sessions(
        user,
        reason="mfa_disabled",
        except_session_id=keep_session_id,  # type: ignore[arg-type]
        ip_address=ip_address,
    )
    record(
        "auth.mfa.disabled",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    queue_email(to=user.email, template="identity/email/mfa_disabled", context={"user": user})


@transaction.atomic
def regenerate_recovery_codes(user: User, *, code: str) -> list[str]:
    device = (
        TOTPDevice.objects.select_for_update().filter(user=user, confirmed_at__isnull=False).first()
    )
    if device is None or not verify_totp(device, code):
        raise InvalidMFACode(_("The verification code is incorrect."), field="code")
    codes = _new_recovery_codes(user)
    record(
        "auth.mfa.recovery_codes_regenerated",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
    )
    return codes
