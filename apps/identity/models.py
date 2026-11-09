"""User accounts. A user is global: access to organisations comes only from memberships.

Design: docs/architecture/05-identity-and-access.md §1.
"""

from datetime import datetime
from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel
from common.validators import validate_e164_phone, validate_timezone


class UserManager(BaseUserManager["User"]):
    use_in_migrations = True

    @classmethod
    def normalize_email(cls, email: str | None) -> str:
        """Emails are stored lower-cased so uniqueness and login are case-insensitive."""
        return (email or "").strip().lower()

    def get_by_natural_key(self, username: str | None) -> User:
        return self.get(email=self.normalize_email(username))

    def _create(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("Users must have an email address.")
        user = self.model(email=self.normalize_email(email), **extra)
        user.set_password(password)  # None gives an unusable password
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        if not (extra["is_staff"] and extra["is_superuser"]):
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create(email, password, **extra)


class User(UUIDPrimaryKeyModel, TimeStampedModel, AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(
        _("email address"),
        max_length=254,
        unique=True,
        error_messages={"unique": _("A user with this email address already exists.")},
    )
    phone = models.CharField(
        _("phone number"),
        max_length=16,
        unique=True,
        null=True,
        blank=True,
        validators=[validate_e164_phone],
        help_text=_("International E.164 format, e.g. +256712345678."),
    )
    # One full-name field rather than first/last: naming conventions vary across markets.
    full_name = models.CharField(_("full name"), max_length=200, blank=True)
    preferred_name = models.CharField(_("preferred name"), max_length=100, blank=True)
    locale = models.CharField(_("language"), max_length=35, blank=True, help_text="BCP 47 tag")
    timezone = models.CharField(
        _("timezone"), max_length=64, blank=True, validators=[validate_timezone]
    )
    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(
        _("Django admin access"),
        default=False,
        help_text=_("Platform staff only. Grants no access to any organisation's data."),
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)
    phone_verified_at = models.DateTimeField(null=True, blank=True)

    objects: ClassVar[UserManager] = UserManager()

    USERNAME_FIELD = "email"
    EMAIL_FIELD = "email"
    REQUIRED_FIELDS: ClassVar[list[str]] = []

    class Meta:
        db_table = "identity_user"
        verbose_name = _("user")
        verbose_name_plural = _("users")
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.CheckConstraint(
                condition=models.Q(email=Lower("email")),
                name="identity_user_email_lowercase",
            ),
            models.CheckConstraint(
                condition=models.Q(phone__isnull=True)
                | models.Q(phone__regex=r"^\+[1-9][0-9]{6,14}$"),
                name="identity_user_phone_e164",
            ),
        ]

    def __str__(self) -> str:
        return self.email

    def _normalise(self) -> None:
        self.email = UserManager.normalize_email(self.email)
        if not self.phone:
            self.phone = None

    def clean(self) -> None:
        super().clean()
        self._normalise()

    def save(self, *args: Any, **kwargs: Any) -> None:
        self._normalise()
        super().save(*args, **kwargs)

    def get_full_name(self) -> str:
        return self.full_name or self.email

    def get_short_name(self) -> str:
        return self.preferred_name or self.full_name or self.email

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def mfa_enabled(self) -> bool:
        device = TOTPDevice.objects.filter(user=self, confirmed_at__isnull=False).first()
        return device is not None


class AuthSession(UUIDPrimaryKeyModel, TimeStampedModel):
    """One signed-in device. Access tokens name the session; revoking it ends them.

    See docs/architecture/adr/0004-authentication-tokens.md.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="auth_sessions")
    device_label = models.CharField(max_length=100, blank=True)
    user_agent = models.CharField(max_length=300, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    # Authentication methods used: "pwd", "otp" (RFC 8176 values).
    amr = ArrayField(models.CharField(max_length=10), default=list)
    auth_time = models.DateTimeField()
    last_used_at = models.DateTimeField()
    expires_at = models.DateTimeField(help_text="Sliding expiry, extended on each refresh.")
    absolute_expires_at = models.DateTimeField(help_text="Hard limit; re-login required.")
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=40, blank=True)

    class Meta:
        db_table = "identity_authsession"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "revoked_at"], name="identity_session_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} {self.device_label or self.user_agent[:40]}"

    def is_usable(self, now: datetime | None = None) -> bool:
        """Can still authenticate requests (access tokens)."""
        now = now or timezone.now()
        return self.revoked_at is None and now < self.absolute_expires_at

    def is_refreshable(self, now: datetime | None = None) -> bool:
        now = now or timezone.now()
        return self.is_usable(now) and now < self.expires_at


class RefreshToken(UUIDPrimaryKeyModel):
    """Each refresh rotates the token. Presenting a rotated token again means it was
    copied, so the whole session is revoked (reuse detection)."""

    session = models.ForeignKey(
        AuthSession, on_delete=models.CASCADE, related_name="refresh_tokens"
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_refreshtoken"

    def __str__(self) -> str:
        return f"refresh token for session {self.session_id}"


class OneTimeToken(UUIDPrimaryKeyModel):
    """Single-use secrets sent by email or returned mid-login. Only the hash is stored."""

    class Purpose(models.TextChoices):
        EMAIL_VERIFICATION = "email_verification", "Email verification"
        EMAIL_CHANGE = "email_change", "Email change"
        PASSWORD_RESET = "password_reset", "Password reset"
        MFA_CHALLENGE = "mfa_challenge", "MFA challenge"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_tokens")
    purpose = models.CharField(max_length=30, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    data = models.JSONField(default=dict, blank=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_onetimetoken"
        indexes: ClassVar[list[models.Index]] = [
            models.Index(fields=["user", "purpose", "used_at"], name="identity_ott_user_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.purpose} for {self.user_id}"

    def is_valid(self, now: datetime | None = None) -> bool:
        return self.used_at is None and (now or timezone.now()) < self.expires_at


class TOTPDevice(UUIDPrimaryKeyModel, TimeStampedModel):
    """Authenticator-app secret, encrypted at rest (apps/identity/crypto.py)."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    encrypted_secret = models.TextField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    # Highest time step accepted so far; a code can never be used twice.
    last_used_step = models.BigIntegerField(null=True, blank=True)

    class Meta:
        db_table = "identity_totpdevice"

    def __str__(self) -> str:
        return f"TOTP for {self.user_id}"


class RecoveryCode(UUIDPrimaryKeyModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64)
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_recoverycode"
        constraints: ClassVar[list[models.BaseConstraint]] = [
            models.UniqueConstraint(fields=["user", "code_hash"], name="identity_recovery_unique"),
        ]

    def __str__(self) -> str:
        return f"recovery code for {self.user_id}"
