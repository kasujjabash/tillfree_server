"""Signed access tokens (JWT). Opaque secrets live in common.tokens."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from django.conf import settings

from common.db.ids import uuid7
from common.tokens import hash_secret as hash_token
from common.tokens import new_opaque_token

__all__ = [
    "AccessClaims",
    "ExpiredAccessToken",
    "InvalidAccessToken",
    "decode_access_token",
    "hash_token",
    "issue_access_token",
    "new_opaque_token",
]

ALGORITHM = "HS256"


# ------------------------------------------------------------------ access tokens
class InvalidAccessToken(Exception):
    pass


class ExpiredAccessToken(InvalidAccessToken):
    pass


@dataclass(frozen=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID
    auth_time: datetime
    amr: tuple[str, ...]
    expires_at: datetime


def _signing_keys() -> dict[str, str]:
    keys: dict[str, str] = settings.JWT_SIGNING_KEYS
    return keys


def issue_access_token(
    *, user_id: UUID, session_id: UUID, auth_time: datetime, amr: list[str], now: datetime
) -> tuple[str, datetime]:
    keys = _signing_keys()
    kid = settings.JWT_ACTIVE_KEY_ID
    expires_at = now + timedelta(seconds=settings.ACCESS_TOKEN_LIFETIME_SECONDS)
    payload = {
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid7()),
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "auth_time": int(auth_time.timestamp()),
        "amr": list(amr),
    }
    token = jwt.encode(payload, keys[kid], algorithm=ALGORITHM, headers={"kid": kid})
    return token, expires_at


def decode_access_token(token: str) -> AccessClaims:
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        key = _signing_keys().get(str(kid))
        if key is None:
            raise InvalidAccessToken("unknown key id")
        payload = jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
            leeway=10,
            options={"require": ["exp", "iat", "sub", "sid", "aud", "iss"]},
        )
        return AccessClaims(
            user_id=UUID(payload["sub"]),
            session_id=UUID(payload["sid"]),
            auth_time=datetime.fromtimestamp(int(payload["auth_time"]), tz=UTC),
            amr=tuple(str(m) for m in payload.get("amr", [])),
            expires_at=datetime.fromtimestamp(int(payload["exp"]), tz=UTC),
        )
    except jwt.ExpiredSignatureError as exc:
        raise ExpiredAccessToken(str(exc)) from exc
    except (jwt.InvalidTokenError, KeyError, ValueError, TypeError) as exc:
        raise InvalidAccessToken(str(exc)) from exc
