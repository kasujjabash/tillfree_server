"""OpenAPI description of the access-token authentication."""

from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AccessTokenScheme(OpenApiAuthenticationExtension):
    target_class = "apps.identity.authentication.AccessTokenAuthentication"
    name = "accessToken"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Access token from /api/v1/auth/login/ (valid 10 minutes).",
        }
