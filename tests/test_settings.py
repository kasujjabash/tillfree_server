from django.conf import settings

from config.settings import base


def test_production_hashes_passwords_with_argon2():
    assert base.PASSWORD_HASHERS[0].endswith("Argon2PasswordHasher")


def test_custom_user_model_is_active():
    assert settings.AUTH_USER_MODEL == "identity.User"


def test_timestamps_are_stored_in_utc():
    assert settings.USE_TZ is True
    assert settings.TIME_ZONE == "UTC"
