"""Identity API request/response shapes. Shape validation only; rules live in services."""

from typing import Any

from rest_framework import serializers

from ..models import AuthSession, User


def password_field(**kwargs: Any) -> serializers.CharField:
    return serializers.CharField(write_only=True, trim_whitespace=False, max_length=128, **kwargs)


class RegisterSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField(max_length=254)
    password = password_field()
    full_name = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class TokenSerializer(serializers.Serializer[Any]):
    token = serializers.CharField(max_length=200)


class EmailSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField(max_length=254)


class LoginSerializer(serializers.Serializer[Any]):
    email = serializers.EmailField(max_length=254)
    password = password_field()
    device_label = serializers.CharField(
        max_length=100, required=False, allow_blank=True, default=""
    )
    use_cookie = serializers.BooleanField(required=False, default=False)


class MFAVerifySerializer(serializers.Serializer[Any]):
    mfa_token = serializers.CharField(max_length=200)
    code = serializers.CharField(max_length=10, required=False, allow_blank=True, default="")
    recovery_code = serializers.CharField(
        max_length=20, required=False, allow_blank=True, default=""
    )
    use_cookie = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
        if not attrs["code"] and not attrs["recovery_code"]:
            raise serializers.ValidationError("Provide a code or a recovery code.", code="required")
        return attrs


class RefreshSerializer(serializers.Serializer[Any]):
    refresh_token = serializers.CharField(
        max_length=200, required=False, allow_blank=True, default=""
    )


class TokenPairSerializer(serializers.Serializer[Any]):
    """Response of sign-in and refresh (documentation only)."""

    token_type = serializers.CharField()
    access_token = serializers.CharField()
    access_token_expires_at = serializers.DateTimeField()
    refresh_token = serializers.CharField(required=False, help_text="Omitted when use_cookie=true.")
    refresh_token_expires_at = serializers.DateTimeField()
    session_id = serializers.UUIDField()


class LoginResponseSerializer(TokenPairSerializer):
    mfa_required = serializers.BooleanField(required=False)
    mfa_token = serializers.CharField(required=False)


class PasswordResetSerializer(serializers.Serializer[Any]):
    token = serializers.CharField(max_length=200)
    new_password = password_field()


class PasswordChangeSerializer(serializers.Serializer[Any]):
    current_password = password_field()
    new_password = password_field()


class EmailChangeSerializer(serializers.Serializer[Any]):
    new_email = serializers.EmailField(max_length=254)
    password = password_field()


class CodeSerializer(serializers.Serializer[Any]):
    code = serializers.CharField(max_length=20)


class MFADisableSerializer(serializers.Serializer[Any]):
    password = password_field()
    code = serializers.CharField(max_length=20, help_text="Authenticator code or a recovery code.")


class TOTPSetupResponseSerializer(serializers.Serializer[Any]):
    secret = serializers.CharField()
    otpauth_uri = serializers.CharField()


class RecoveryCodesSerializer(serializers.Serializer[Any]):
    recovery_codes = serializers.ListField(child=serializers.CharField())


class DetailSerializer(serializers.Serializer[Any]):
    detail = serializers.CharField()


class MeSerializer(serializers.ModelSerializer[User]):
    email_verified = serializers.BooleanField(read_only=True)
    phone_verified = serializers.SerializerMethodField()
    mfa_enabled = serializers.BooleanField(read_only=True)

    class Meta:
        model = User
        fields = (
            "id",
            "email",
            "email_verified",
            "phone",
            "phone_verified",
            "full_name",
            "preferred_name",
            "locale",
            "timezone",
            "mfa_enabled",
            "created_at",
        )
        read_only_fields = ("id", "email", "created_at")
        # Model validators (phone, timezone) run in the service via full_clean.
        extra_kwargs: dict[str, Any] = {"phone": {"validators": []}}

    def get_phone_verified(self, obj: User) -> bool:
        return obj.phone_verified_at is not None


class SessionSerializer(serializers.ModelSerializer[AuthSession]):
    current = serializers.SerializerMethodField()

    class Meta:
        model = AuthSession
        fields = (
            "id",
            "device_label",
            "user_agent",
            "ip_address",
            "amr",
            "created_at",
            "last_used_at",
            "current",
        )

    def get_current(self, obj: AuthSession) -> bool:
        return obj.id == self.context.get("current_session_id")
