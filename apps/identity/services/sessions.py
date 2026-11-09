"""Sessions, token issuing, refresh rotation and revocation (ADR-0004)."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.audit.public import record
from common.exceptions import DomainError

from ..models import AuthSession, RefreshToken, User
from ..tokens import hash_token, issue_access_token, new_opaque_token


class InvalidRefreshToken(DomainError):
    status_code = 401
    default_code = "invalid_refresh_token"
    title = _("The refresh token is invalid or has expired. Sign in again.")


@dataclass(frozen=True)
class IssuedTokens:
    access_token: str
    access_token_expires_at: datetime
    refresh_token: str
    refresh_token_expires_at: datetime
    session_id: UUID


@dataclass(frozen=True)
class ClientInfo:
    ip_address: str | None = None
    user_agent: str = ""
    device_label: str = ""


def _sliding() -> timedelta:
    return timedelta(seconds=settings.REFRESH_TOKEN_SLIDING_SECONDS)


def _absolute() -> timedelta:
    return timedelta(seconds=settings.REFRESH_TOKEN_ABSOLUTE_SECONDS)


def _issue(session: AuthSession, now: datetime) -> IssuedTokens:
    raw, token_hash = new_opaque_token()
    RefreshToken.objects.create(session=session, token_hash=token_hash)
    access, access_expires = issue_access_token(
        user_id=session.user_id,
        session_id=session.id,
        auth_time=session.auth_time,
        amr=list(session.amr),
        now=now,
    )
    return IssuedTokens(
        access_token=access,
        access_token_expires_at=access_expires,
        refresh_token=raw,
        refresh_token_expires_at=min(session.expires_at, session.absolute_expires_at),
        session_id=session.id,
    )


@transaction.atomic
def start_session(user: User, *, amr: Sequence[str], client: ClientInfo) -> IssuedTokens:
    now = timezone.now()
    session = AuthSession.objects.create(
        user=user,
        device_label=client.device_label[:100],
        user_agent=client.user_agent[:300],
        ip_address=client.ip_address,
        amr=list(amr),
        auth_time=now,
        last_used_at=now,
        expires_at=now + _sliding(),
        absolute_expires_at=now + _absolute(),
    )
    User.objects.filter(pk=user.pk).update(last_login=now)
    record(
        "auth.session.started",
        actor_id=user.id,
        resource_type="auth_session",
        resource_id=session.id,
        metadata={"amr": list(amr)},
        ip_address=client.ip_address,
    )
    return _issue(session, now)


def refresh_session(raw_refresh_token: str, *, client: ClientInfo) -> IssuedTokens:
    """Rotate a refresh token. Reusing an already-rotated token revokes the session."""
    now = timezone.now()
    with transaction.atomic():
        token = (
            RefreshToken.objects.select_for_update()
            .select_related("session", "session__user")
            .filter(token_hash=hash_token(raw_refresh_token))
            .first()
        )
        if token is None:
            raise InvalidRefreshToken(_("The refresh token is invalid or has expired."))
        session = token.session
        if token.rotated_at is not None:
            # Copied or replayed token: end the session for everyone holding it.
            if session.revoked_at is None:
                _revoke(session, reason="refresh_token_reuse", now=now)
                record(
                    "auth.refresh_token.reused",
                    actor_id=session.user_id,
                    resource_type="auth_session",
                    resource_id=session.id,
                    ip_address=client.ip_address,
                )
        elif not session.is_refreshable(now) or not session.user.is_active:
            raise InvalidRefreshToken(_("The refresh token is invalid or has expired."))
        else:
            token.rotated_at = now
            token.save(update_fields=["rotated_at"])
            session.last_used_at = now
            session.expires_at = min(now + _sliding(), session.absolute_expires_at)
            session.save(update_fields=["last_used_at", "expires_at", "updated_at"])
            return _issue(session, now)
    # Raised after the transaction commits so the revocation is kept.
    raise InvalidRefreshToken(_("This refresh token was already used. Sign in again."))


def _revoke(session: AuthSession, *, reason: str, now: datetime) -> None:
    session.revoked_at = now
    session.revoked_reason = reason
    session.save(update_fields=["revoked_at", "revoked_reason", "updated_at"])


@transaction.atomic
def revoke_session(
    session: AuthSession, *, reason: str, actor_id: UUID | None, ip_address: str | None = None
) -> None:
    if session.revoked_at is not None:
        return
    _revoke(session, reason=reason, now=timezone.now())
    record(
        "auth.session.revoked",
        actor_id=actor_id,
        resource_type="auth_session",
        resource_id=session.id,
        metadata={"reason": reason, "user_id": str(session.user_id)},
        ip_address=ip_address,
    )


@transaction.atomic
def revoke_all_sessions(
    user: User,
    *,
    reason: str,
    except_session_id: UUID | None = None,
    actor_id: UUID | None = None,
    ip_address: str | None = None,
) -> int:
    sessions = AuthSession.objects.filter(user=user, revoked_at__isnull=True)
    if except_session_id is not None:
        sessions = sessions.exclude(pk=except_session_id)
    count = sessions.update(revoked_at=timezone.now(), revoked_reason=reason)
    if count:
        record(
            "auth.session.revoked_all",
            actor_id=actor_id or user.id,
            resource_type="user",
            resource_id=user.id,
            metadata={"reason": reason, "count": count},
            ip_address=ip_address,
        )
    return count


def get_user_session(user: User, session_id: UUID | str) -> AuthSession | None:
    """A session owned by ``user``, or None (never another user's session)."""
    return AuthSession.objects.filter(pk=session_id, user=user).first()


def end_session(user: User, session_id: UUID | str, *, reason: str, ip_address: str | None) -> bool:
    """Revoke one of the user's own sessions. Returns False if it is not theirs."""
    session = get_user_session(user, session_id)
    if session is None:
        return False
    revoke_session(session, reason=reason, actor_id=user.id, ip_address=ip_address)
    return True


def active_sessions(user: User) -> list[AuthSession]:
    now = timezone.now()
    return list(
        AuthSession.objects.filter(
            user=user, revoked_at__isnull=True, absolute_expires_at__gt=now, expires_at__gt=now
        ).order_by("-last_used_at")
    )
