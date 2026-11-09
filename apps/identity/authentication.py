from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.request import Request

from .models import AuthSession, User
from .tokens import ExpiredAccessToken, InvalidAccessToken, decode_access_token


@dataclass(frozen=True)
class AuthInfo:
    """Attached to ``request.auth`` for token-authenticated requests."""

    session_id: UUID
    amr: tuple[str, ...]
    auth_time: datetime

    @property
    def mfa_satisfied(self) -> bool:
        return "otp" in self.amr


class AccessTokenAuthentication(BaseAuthentication):
    """``Authorization: Bearer <access token>``.

    Besides the signature and expiry, every request checks the session row (one
    primary-key query that also loads the user), so sign-out and revocation take effect
    immediately rather than when the token expires.
    """

    keyword = b"bearer"

    def authenticate(self, request: Request) -> tuple[User, AuthInfo] | None:
        parts = get_authorization_header(request).split()
        if not parts or parts[0].lower() != self.keyword:
            return None
        if len(parts) != 2:
            raise AuthenticationFailed(_("Malformed Authorization header."), code="invalid_token")
        try:
            token = parts[1].decode("ascii")
            claims = decode_access_token(token)
        except ExpiredAccessToken as exc:
            raise AuthenticationFailed(
                _("The access token has expired."), code="token_expired"
            ) from exc
        except (InvalidAccessToken, UnicodeDecodeError) as exc:
            raise AuthenticationFailed(
                _("The access token is invalid."), code="invalid_token"
            ) from exc

        session = (
            AuthSession.objects.select_related("user")
            .filter(pk=claims.session_id, user_id=claims.user_id)
            .first()
        )
        if session is None or not session.is_usable(timezone.now()):
            raise AuthenticationFailed(
                _("This session has ended. Sign in again."), code="session_revoked"
            )
        if not session.user.is_active:
            raise AuthenticationFailed(_("This account is disabled."), code="account_disabled")
        return session.user, AuthInfo(
            session_id=session.id, amr=tuple(session.amr), auth_time=session.auth_time
        )

    def authenticate_header(self, request: Request) -> str:
        return 'Bearer realm="api"'
