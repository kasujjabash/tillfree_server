"""Opaque secrets sent to people (email links, refresh tokens, invitations).

Only a SHA-256 hash is stored. The secrets are 256-bit random, so a fast hash is
appropriate (unlike passwords, they cannot be guessed).
"""

import hashlib
import secrets


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def new_opaque_token() -> tuple[str, str]:
    """Return ``(raw, hash)``."""
    raw = new_secret()
    return raw, hash_secret(raw)
