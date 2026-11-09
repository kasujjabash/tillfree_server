import pytest
from django.contrib.auth import authenticate
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from apps.identity.models import User


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user("Amina@Example.COM", password="correct-horse-battery")


def test_primary_key_is_uuid_v7(user):
    assert user.id.version == 7


def test_email_is_normalised_to_lowercase(user):
    assert user.email == "amina@example.com"


def test_login_is_case_insensitive(user):
    assert authenticate(username="AMINA@example.com", password="correct-horse-battery") == user


def test_wrong_password_fails(user):
    assert authenticate(username="amina@example.com", password="wrong") is None


def test_email_is_unique_regardless_of_case(user):
    with pytest.raises(ValidationError):
        User.objects.create_user("amina@EXAMPLE.com", password="another-password-1")


def test_database_rejects_uppercase_email(user):
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email="UPPER@EXAMPLE.COM")


def test_valid_e164_phone_is_accepted(db):
    user = User.objects.create_user("p@example.com", phone="+256712345678")
    assert user.phone == "+256712345678"


@pytest.mark.parametrize("phone", ["0712345678", "+256 712 345 678", "+2561", "not a phone"])
def test_invalid_phone_is_rejected(db, phone):
    with pytest.raises(ValidationError) as exc:
        User.objects.create_user("p@example.com", phone=phone)
    assert "phone" in exc.value.message_dict


def test_blank_phone_is_stored_as_null_so_many_users_can_omit_it(db):
    User.objects.create_user("one@example.com", phone="")
    User.objects.create_user("two@example.com", phone="")
    assert User.objects.filter(phone__isnull=True).count() == 2


def test_invalid_timezone_is_rejected(db):
    with pytest.raises(ValidationError):
        User.objects.create_user("tz@example.com", timezone="Mars/Olympus")


def test_user_without_password_cannot_log_in(db):
    user = User.objects.create_user("invited@example.com")
    assert not user.has_usable_password()


def test_superuser_flags(db):
    admin = User.objects.create_superuser("root@example.com", password="a-long-password-1")
    assert admin.is_staff and admin.is_superuser
