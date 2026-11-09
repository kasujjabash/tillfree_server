import pytest

from apps.organisations.models import Organisation


@pytest.fixture
def org_a(db) -> Organisation:
    return Organisation.objects.create(name="Org A", slug="org-a")


@pytest.fixture
def org_b(db) -> Organisation:
    return Organisation.objects.create(name="Org B", slug="org-b")
