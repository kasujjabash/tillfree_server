"""Identity endpoints: /api/v1/auth/... and /api/v1/me/..."""

from typing import Any
from uuid import UUID

from django.utils.translation import gettext_lazy as _
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotAuthenticated
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle, UserRateThrottle
from rest_framework.views import APIView

from common.api.request import client_ip, user_agent
from common.exceptions import NotFoundError

from ..authentication import AuthInfo
from ..models import User
from ..services import accounts, authentication, mfa, sessions
from . import serializers as s
from .responses import clear_refresh_cookie, refresh_cookie_value, token_response

ACCEPTED = _("If the details are valid, we have sent an email with the next step.")


def _client(request: Request, device_label: str = "") -> sessions.ClientInfo:
    return sessions.ClientInfo(
        ip_address=client_ip(request), user_agent=user_agent(request), device_label=device_label
    )


def _auth(request: Request) -> AuthInfo:
    if not isinstance(request.auth, AuthInfo):
        raise NotAuthenticated()
    return request.auth


def _user(request: Request) -> User:
    if not isinstance(request.user, User):
        raise NotAuthenticated()
    return request.user


class PublicAuthView(APIView):
    """Unauthenticated endpoint with a per-IP scoped rate limit."""

    authentication_classes = ()
    permission_classes = (AllowAny,)
    throttle_classes = (
        ScopedRateThrottle,
        AnonRateThrottle,
    )


class AuthenticatedView(APIView):
    permission_classes = (IsAuthenticated,)
    throttle_classes = (
        ScopedRateThrottle,
        UserRateThrottle,
    )


# ------------------------------------------------------------------ registration
class RegisterView(PublicAuthView):
    throttle_scope = "auth_register"

    @extend_schema(request=s.RegisterSerializer, responses={202: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.RegisterSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.register(**data.validated_data, ip_address=client_ip(request))
        return Response({"detail": ACCEPTED}, status=status.HTTP_202_ACCEPTED)


class VerifyEmailView(PublicAuthView):
    throttle_scope = "auth_token"

    @extend_schema(request=s.TokenSerializer, responses={200: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.TokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.verify_email(data.validated_data["token"], ip_address=client_ip(request))
        return Response({"detail": _("Email address verified.")})


class ResendVerificationView(PublicAuthView):
    throttle_scope = "auth_email"

    @extend_schema(request=s.EmailSerializer, responses={202: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.EmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.resend_verification(data.validated_data["email"])
        return Response({"detail": ACCEPTED}, status=status.HTTP_202_ACCEPTED)


# ------------------------------------------------------------------ sign-in
class LoginView(PublicAuthView):
    throttle_scope = "auth_login"

    @extend_schema(request=s.LoginSerializer, responses={200: s.LoginResponseSerializer})
    def post(self, request: Request) -> Response:
        data = s.LoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        result = authentication.login(
            email=v["email"],
            password=v["password"],
            client=_client(request, v["device_label"]),
            request=request._request,
        )
        if result.mfa_token:
            return Response({"mfa_required": True, "mfa_token": result.mfa_token})
        if result.tokens is None:  # pragma: no cover - login returns one of the two
            raise RuntimeError("login returned neither tokens nor an MFA challenge")
        return token_response(result.tokens, use_cookie=v["use_cookie"])


class MFAVerifyView(PublicAuthView):
    throttle_scope = "auth_mfa"

    @extend_schema(request=s.MFAVerifySerializer, responses={200: s.TokenPairSerializer})
    def post(self, request: Request) -> Response:
        data = s.MFAVerifySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        v = data.validated_data
        tokens = authentication.complete_mfa_login(
            mfa_token=v["mfa_token"],
            code=v["code"],
            recovery_code=v["recovery_code"],
            client=_client(request),
        )
        return token_response(tokens, use_cookie=v["use_cookie"])


class RefreshView(PublicAuthView):
    """Body token (mobile) or HttpOnly cookie (web). The response uses the same channel."""

    throttle_scope = "auth_refresh"

    @extend_schema(request=s.RefreshSerializer, responses={200: s.TokenPairSerializer})
    def post(self, request: Request) -> Response:
        data = s.RefreshSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        body_token = data.validated_data["refresh_token"]
        cookie_token = refresh_cookie_value(request._request)
        raw = body_token or cookie_token
        if not raw:
            raise sessions.InvalidRefreshToken(_("No refresh token was provided."))
        tokens = sessions.refresh_session(raw, client=_client(request))
        return token_response(tokens, use_cookie=not body_token)


class LogoutView(AuthenticatedView):
    throttle_scope = "auth_refresh"

    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        sessions.end_session(
            _user(request),
            _auth(request).session_id,
            reason="logout",
            ip_address=client_ip(request),
        )
        return clear_refresh_cookie(Response(status=status.HTTP_204_NO_CONTENT))


class LogoutAllView(AuthenticatedView):
    throttle_scope = "auth_refresh"

    @extend_schema(request=None, responses={204: None})
    def post(self, request: Request) -> Response:
        sessions.revoke_all_sessions(
            _user(request), reason="logout_all", ip_address=client_ip(request)
        )
        return clear_refresh_cookie(Response(status=status.HTTP_204_NO_CONTENT))


# ------------------------------------------------------------------ passwords
class PasswordForgotView(PublicAuthView):
    throttle_scope = "auth_email"

    @extend_schema(request=s.EmailSerializer, responses={202: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.EmailSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.request_password_reset(data.validated_data["email"], ip_address=client_ip(request))
        return Response({"detail": ACCEPTED}, status=status.HTTP_202_ACCEPTED)


class PasswordResetView(PublicAuthView):
    throttle_scope = "auth_token"

    @extend_schema(request=s.PasswordResetSerializer, responses={200: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.PasswordResetSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.reset_password(
            data.validated_data["token"],
            data.validated_data["new_password"],
            ip_address=client_ip(request),
        )
        return Response({"detail": _("Password changed. Sign in with your new password.")})


class PasswordChangeView(AuthenticatedView):
    throttle_scope = "auth_sensitive"

    @extend_schema(request=s.PasswordChangeSerializer, responses={200: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.PasswordChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.change_password(
            _user(request),
            **data.validated_data,
            keep_session_id=_auth(request).session_id,
            ip_address=client_ip(request),
        )
        return Response({"detail": _("Password changed. Other devices were signed out.")})


# ------------------------------------------------------------------ email change
class EmailChangeView(AuthenticatedView):
    throttle_scope = "auth_email"

    @extend_schema(request=s.EmailChangeSerializer, responses={202: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.EmailChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.request_email_change(
            _user(request), **data.validated_data, ip_address=client_ip(request)
        )
        return Response({"detail": ACCEPTED}, status=status.HTTP_202_ACCEPTED)


class EmailChangeConfirmView(PublicAuthView):
    throttle_scope = "auth_token"

    @extend_schema(request=s.TokenSerializer, responses={200: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.TokenSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        accounts.confirm_email_change(data.validated_data["token"], ip_address=client_ip(request))
        return Response({"detail": _("Email address changed. Sign in again.")})


# ------------------------------------------------------------------ MFA management
class TOTPSetupView(AuthenticatedView):
    throttle_scope = "auth_sensitive"

    @extend_schema(request=None, responses={200: s.TOTPSetupResponseSerializer})
    def post(self, request: Request) -> Response:
        secret, uri = mfa.begin_totp_setup(_user(request))
        response = Response({"secret": secret, "otpauth_uri": uri})
        response["Cache-Control"] = "no-store"
        return response


class TOTPConfirmView(AuthenticatedView):
    throttle_scope = "auth_mfa"

    @extend_schema(request=s.CodeSerializer, responses={200: s.RecoveryCodesSerializer})
    def post(self, request: Request) -> Response:
        data = s.CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        codes = mfa.confirm_totp_setup(
            _user(request), data.validated_data["code"], ip_address=client_ip(request)
        )
        response = Response({"recovery_codes": codes})
        response["Cache-Control"] = "no-store"
        return response


class TOTPDisableView(AuthenticatedView):
    throttle_scope = "auth_mfa"

    @extend_schema(request=s.MFADisableSerializer, responses={200: s.DetailSerializer})
    def post(self, request: Request) -> Response:
        data = s.MFADisableSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        mfa.disable_totp(
            _user(request),
            **data.validated_data,
            keep_session_id=_auth(request).session_id,
            ip_address=client_ip(request),
        )
        return Response({"detail": _("Multi-factor authentication disabled.")})


class RecoveryCodesView(AuthenticatedView):
    throttle_scope = "auth_mfa"

    @extend_schema(request=s.CodeSerializer, responses={200: s.RecoveryCodesSerializer})
    def post(self, request: Request) -> Response:
        data = s.CodeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        codes = mfa.regenerate_recovery_codes(_user(request), code=data.validated_data["code"])
        response = Response({"recovery_codes": codes})
        response["Cache-Control"] = "no-store"
        return response


# ------------------------------------------------------------------ me
class MeView(AuthenticatedView):
    throttle_scope = "me"

    @extend_schema(responses={200: s.MeSerializer})
    def get(self, request: Request) -> Response:
        return Response(s.MeSerializer(_user(request)).data)

    @extend_schema(request=s.MeSerializer, responses={200: s.MeSerializer})
    def patch(self, request: Request) -> Response:
        data = s.MeSerializer(_user(request), data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        user = accounts.update_profile(_user(request), dict(data.validated_data))
        return Response(s.MeSerializer(user).data)


class SessionListView(AuthenticatedView):
    throttle_scope = "me"

    @extend_schema(responses={200: s.SessionSerializer(many=True)})
    def get(self, request: Request) -> Response:
        items = sessions.active_sessions(_user(request))
        context: dict[str, Any] = {"current_session_id": _auth(request).session_id}
        return Response(s.SessionSerializer(items, many=True, context=context).data)


class SessionDetailView(AuthenticatedView):
    throttle_scope = "me"

    @extend_schema(responses={204: None})
    def delete(self, request: Request, session_id: UUID) -> Response:
        ended = sessions.end_session(
            _user(request), session_id, reason="revoked_by_user", ip_address=client_ip(request)
        )
        if not ended:
            raise NotFoundError(_("Session not found."))
        return Response(status=status.HTTP_204_NO_CONTENT)
