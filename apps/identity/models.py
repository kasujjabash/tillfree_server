"""User accounts. A user is global: access to organisations comes only from memberships.

Design: docs/architecture/05-identity-and-access.md §1.
"""

from typing import Any, ClassVar

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models.functions import Lower
from django.utils.translation import gettext_lazy as _

from common.db.models import TimeStampedModel, UUIDPrimaryKeyModel

from .validators import validate_e164_phone, validate_timezone


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
