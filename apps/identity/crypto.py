"""Encryption of small secrets at rest (TOTP seeds).

Uses Fernet (AES-128-CBC + HMAC-SHA256). ``FIELD_ENCRYPTION_KEYS`` is a list: the
first key encrypts, all keys decrypt, so keys can be rotated without downtime.
"""

from functools import cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings


@cache
def _fernet(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(key.encode()) for key in keys])


def _current() -> MultiFernet:
    return _fernet(tuple(settings.FIELD_ENCRYPTION_KEYS))


def encrypt(plaintext: str) -> str:
    return _current().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _current().decrypt(ciphertext.encode()).decode()
