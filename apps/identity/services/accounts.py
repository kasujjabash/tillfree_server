"""Registration, email verification, password and profile management.

Responses to "is this email registered?"-shaped requests never differ between
existing and unknown addresses (registration, resend, forgot-password): the email
inbox is the only place that learns the answer.
"""

from datetime import timedelta
from typing import Any
from uuid import UUID

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from common.exceptions import BusinessRuleViolation, ConflictError
from common.mail import frontend_url, queue_email

from .. import login_protection
from ..models import OneTimeToken, User, UserManager
from ..tokens import hash_token, new_opaque_token
from .sessions import revoke_all_sessions

EMAIL_VERIFICATION_TTL = timedelta(hours=48)
EMAIL_CHANGE_TTL = timedelta(hours=24)
PASSWORD_RESET_TTL = timedelta(minutes=30)

PROFILE_FIELDS = ("full_name", "preferred_name", "phone", "locale", "timezone")


class InvalidToken(BusinessRuleViolation):
    default_code = "invalid_token"
    title = _("The link is invalid or has expired.")


def _issue_token(
    user: User, purpose: str, ttl: timedelta, data: dict[str, Any] | None = None
) -> str:
    """Create a single-use token, invalidating earlier unused ones of the same purpose."""
    now = timezone.now()
    OneTimeToken.objects.filter(user=user, purpose=purpose, used_at__isnull=True).update(
        used_at=now
    )
    raw, token_hash = new_opaque_token()
    OneTimeToken.objects.create(
        user=user, purpose=purpose, token_hash=token_hash, expires_at=now + ttl, data=data or {}
    )
    return raw


def consume_token(raw: str, purpose: str) -> OneTimeToken:
    """Validate and mark a token used, atomically. Raises ``InvalidToken``."""
    token = (
        OneTimeToken.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=hash_token(raw), purpose=purpose)
        .first()
    )
    if token is None or not token.is_valid() or not token.user.is_active:
        raise InvalidToken(_("The link is invalid or has expired."))
    token.used_at = timezone.now()
    token.save(update_fields=["used_at"])
    return token


def _send_verification(user: User) -> None:
    raw = _issue_token(user, OneTimeToken.Purpose.EMAIL_VERIFICATION, EMAIL_VERIFICATION_TTL)
    queue_email(
        to=user.email,
        template="identity/email/verify_email",
        context={"user": user, "url": frontend_url("auth/verify-email", token=raw)},
    )


# ------------------------------------------------------------------ registration
@transaction.atomic
def register(
    *, email: str, password: str, full_name: str = "", ip_address: str | None = None
) -> None:
    email = UserManager.normalize_email(email)
    existing = User.objects.filter(email=email).first()
    if existing is not None:
        # Tell the owner of the address, not the caller.
        queue_email(
            to=existing.email,
            template="identity/email/already_registered",
            context={"user": existing, "url": frontend_url("auth/sign-in")},
        )
        return
    candidate = User(email=email, full_name=full_name)
    validate_password(password, user=candidate)
    user = User.objects.create_user(email, password, full_name=full_name)
    record(
        "user.registered",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    _send_verification(user)


@transaction.atomic
def verify_email(raw_token: str, *, ip_address: str | None = None) -> User:
    token = consume_token(raw_token, OneTimeToken.Purpose.EMAIL_VERIFICATION)
    user = token.user
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=["email_verified_at", "updated_at"])
        record(
            "user.email_verified",
            actor_id=user.id,
            resource_type="user",
            resource_id=user.id,
            ip_address=ip_address,
        )
    return user


@transaction.atomic
def resend_verification(email: str) -> None:
    user = User.objects.filter(email=UserManager.normalize_email(email), is_active=True).first()
    if user is not None and user.email_verified_at is None:
        _send_verification(user)


# ------------------------------------------------------------------ passwords
@transaction.atomic
def request_password_reset(email: str, *, ip_address: str | None = None) -> None:
    user = User.objects.filter(email=UserManager.normalize_email(email), is_active=True).first()
    if user is None:
        return
    raw = _issue_token(user, OneTimeToken.Purpose.PASSWORD_RESET, PASSWORD_RESET_TTL)
    queue_email(
        to=user.email,
        template="identity/email/password_reset",
        context={
            "user": user,
            "url": frontend_url("auth/reset-password", token=raw),
            "minutes": int(PASSWORD_RESET_TTL.total_seconds() // 60),
        },
    )
    record(
        "auth.password.reset_requested",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )


@transaction.atomic
def reset_password(raw_token: str, new_password: str, *, ip_address: str | None = None) -> User:
    token = consume_token(raw_token, OneTimeToken.Purpose.PASSWORD_RESET)
    user = token.user
    validate_password(new_password, user=user)
    user.set_password(new_password)
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()  # they proved control of the inbox
    user.save(update_fields=["password", "email_verified_at", "updated_at"])
    revoke_all_sessions(user, reason="password_reset", ip_address=ip_address)
    login_protection.clear(user.email)
    record(
        "auth.password.reset",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    _notify_password_changed(user)
    return user


@transaction.atomic
def change_password(
    user: User,
    *,
    current_password: str,
    new_password: str,
    keep_session_id: UUID | None,
    ip_address: str | None = None,
) -> None:
    if not user.check_password(current_password):
        raise BusinessRuleViolation(
            _("The current password is incorrect."),
            code="invalid_password",
            field="current_password",
        )
    try:
        validate_password(new_password, user=user)
    except ValidationError as exc:
        raise ValidationError({"new_password": exc.messages}) from exc
    user.set_password(new_password)
    user.save(update_fields=["password", "updated_at"])
    revoke_all_sessions(
        user, reason="password_changed", except_session_id=keep_session_id, ip_address=ip_address
    )
    record(
        "auth.password.changed",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    _notify_password_changed(user)


def _notify_password_changed(user: User) -> None:
    queue_email(
        to=user.email,
        template="identity/email/password_changed",
        context={"user": user, "url": frontend_url("auth/forgot-password")},
    )


# ------------------------------------------------------------------ profile
@transaction.atomic
def update_profile(user: User, changes: dict[str, Any]) -> User:
    changed = [f for f in PROFILE_FIELDS if f in changes and getattr(user, f) != changes[f]]
    for field in changed:
        setattr(user, field, changes[field])
    if "phone" in changed:
        user.phone_verified_at = None
    if not changed:
        return user
    user.full_clean(exclude=["password"])
    user.save()
    record(
        "user.profile_updated",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        metadata={"fields": changed},
    )
    return user


# ------------------------------------------------------------------ email change
@transaction.atomic
def request_email_change(
    user: User, *, new_email: str, password: str, ip_address: str | None = None
) -> None:
    """Send a confirmation link to the new address; the old one is told about it."""
    if not user.check_password(password):
        raise BusinessRuleViolation(
            _("The password is incorrect."), code="invalid_password", field="password"
        )
    new_email = UserManager.normalize_email(new_email)
    if new_email == user.email:
        raise BusinessRuleViolation(
            _("That is already your email address."), code="unchanged", field="new_email"
        )
    if User.objects.filter(email=new_email).exists():
        # Do not reveal that the address is registered; nothing is sent.
        return
    raw = _issue_token(
        user, OneTimeToken.Purpose.EMAIL_CHANGE, EMAIL_CHANGE_TTL, data={"new_email": new_email}
    )
    queue_email(
        to=new_email,
        template="identity/email/confirm_email_change",
        context={"user": user, "url": frontend_url("auth/confirm-email-change", token=raw)},
    )
    queue_email(
        to=user.email,
        template="identity/email/email_change_requested",
        context={"user": user, "new_email": new_email},
    )
    record(
        "user.email_change_requested",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )


@transaction.atomic
def confirm_email_change(raw_token: str, *, ip_address: str | None = None) -> User:
    token = consume_token(raw_token, OneTimeToken.Purpose.EMAIL_CHANGE)
    user = token.user
    new_email = str(token.data.get("new_email", ""))
    if not new_email or User.objects.filter(email=new_email).exclude(pk=user.pk).exists():
        raise ConflictError(_("That email address is no longer available."), code="email_taken")
    old_email = user.email
    user.email = new_email
    user.email_verified_at = timezone.now()
    user.save(update_fields=["email", "email_verified_at", "updated_at"])
    revoke_all_sessions(user, reason="email_changed", ip_address=ip_address)
    record(
        "user.email_changed",
        actor_id=user.id,
        resource_type="user",
        resource_id=user.id,
        ip_address=ip_address,
    )
    queue_email(
        to=old_email,
        template="identity/email/email_changed",
        context={"user": user, "url": frontend_url("auth/forgot-password")},
    )
    return user
