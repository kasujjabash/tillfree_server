from collections.abc import Callable, Iterator
from typing import Any

import pyotp
import pytest
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APIClient

from apps.access.models import Membership, PlatformStaff, RoleAssignment
from apps.access.services.onboarding import create_organisation
from apps.identity.crypto import encrypt
from apps.identity.models import TOTPDevice, User
from apps.identity.services.sessions import ClientInfo, start_session
from apps.organisations.models import Organisation
from common.tenancy.context import tenant_context

PASSWORD = "Correct-Horse-Battery-9"  # noqa: S105 - test fixture
ORG_DATA = {"country_code": "UG", "base_currency": "UGX", "default_timezone": "Africa/Kampala"}


@pytest.fixture(autouse=True)
def _clear_cache() -> Iterator[None]:
    """Throttle and login-protection counters must not leak between tests."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def org_a(db) -> Organisation:
    return Organisation.objects.create(name="Org A", slug="org-a", **ORG_DATA)


@pytest.fixture
def org_b(db) -> Organisation:
    return Organisation.objects.create(name="Org B", slug="org-b", **ORG_DATA)


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def make_user(db) -> Callable[..., User]:
    def make(
        email: str = "user@example.com",
        *,
        verified: bool = True,
        mfa_secret: str | None = None,
        **extra: Any,
    ) -> User:
        user = User.objects.create_user(email, PASSWORD, **extra)
        if verified:
            user.email_verified_at = timezone.now()
            user.save(update_fields=["email_verified_at"])
        if mfa_secret:
            TOTPDevice.objects.create(
                user=user, encrypted_secret=encrypt(mfa_secret), confirmed_at=timezone.now()
            )
        return user

    return make


@pytest.fixture
def client_for(api: APIClient) -> Callable[..., APIClient]:
    """An API client signed in as ``user`` (optionally with MFA) and inside ``org``."""

    def build(user: User, org: Organisation | None = None, *, mfa: bool = False) -> APIClient:
        tokens = start_session(user, amr=["pwd", "otp"] if mfa else ["pwd"], client=ClientInfo())
        client = APIClient()
        client.credentials(
            HTTP_AUTHORIZATION=f"Bearer {tokens.access_token}",
            **({"HTTP_X_ORGANISATION_ID": str(org.id)} if org else {}),
        )
        return client

    return build


@pytest.fixture
def new_org(make_user) -> Callable[..., tuple[Organisation, User]]:
    """Create an organisation through onboarding; returns (organisation, owner)."""

    def make(
        name: str = "Kampala Clinic", owner_email: str = "owner@example.com"
    ) -> tuple[Organisation, User]:
        owner = make_user(owner_email)
        return create_organisation(owner, {"name": name, **ORG_DATA}), owner

    return make


@pytest.fixture
def add_member(make_user) -> Callable[..., tuple[User, Membership]]:
    """Add a user to ``org`` with a role template (and optional branch)."""

    def add(
        org: Organisation,
        email: str,
        template_key: str | None = "staff",
        *,
        role: Any = None,
        branch: Any = None,
        mfa_secret: str | None = None,
    ) -> tuple[User, Membership]:
        user = make_user(email, mfa_secret=mfa_secret)
        with tenant_context(org.id):
            membership = Membership.objects.create(user=user)
            if template_key or role:
                RoleAssignment.objects.create(
                    membership=membership,
                    template_key=None if role else template_key,
                    role=role,
                    branch=branch,
                )
        return user, membership

    return add


@pytest.fixture
def make_staff(make_user) -> Callable[..., User]:
    def make(email: str = "support@tillfree.test", role: str = "support_admin") -> User:
        user = make_user(email)
        PlatformStaff.objects.create(user=user, role=role)
        return user

    return make


@pytest.fixture
def totp_secret() -> str:
    return pyotp.random_base32()


def totp_now(secret: str) -> str:
    return pyotp.TOTP(secret).now()
