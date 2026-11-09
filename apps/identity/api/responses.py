"""How issued tokens are returned to clients.

Mobile apps receive the refresh token in the JSON body and keep it in the OS keystore.
Web apps pass ``use_cookie: true``: the refresh token is then set as an HttpOnly,
SameSite=Strict cookie limited to the auth endpoints, so page JavaScript can never
read it (XSS cannot steal it).
"""

from django.conf import settings
from django.http import HttpRequest
from rest_framework import status as http_status
from rest_framework.response import Response

from ..services.sessions import IssuedTokens


def refresh_cookie_value(request: HttpRequest) -> str | None:
    value = request.COOKIES.get(settings.REFRESH_COOKIE_NAME)
    return str(value) if value else None


def token_response(
    tokens: IssuedTokens, *, use_cookie: bool, status: int = http_status.HTTP_200_OK
) -> Response:
    body = {
        "token_type": "Bearer",
        "access_token": tokens.access_token,
        "access_token_expires_at": tokens.access_token_expires_at,
        "refresh_token_expires_at": tokens.refresh_token_expires_at,
        "session_id": str(tokens.session_id),
    }
    if not use_cookie:
        body["refresh_token"] = tokens.refresh_token
    response = Response(body, status=status)
    response["Cache-Control"] = "no-store"
    if use_cookie:
        response.set_cookie(
            settings.REFRESH_COOKIE_NAME,
            tokens.refresh_token,
            expires=tokens.refresh_token_expires_at,
            path=settings.REFRESH_COOKIE_PATH,
            secure=settings.REFRESH_COOKIE_SECURE,
            httponly=True,
            samesite="Strict",
        )
    return response


def clear_refresh_cookie(response: Response) -> Response:
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME, path=settings.REFRESH_COOKIE_PATH, samesite="Strict"
    )
    return response
