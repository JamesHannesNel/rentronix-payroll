import pytest
from rest_framework.test import APIClient

from apps.core.models import ApiKey, Company


@pytest.fixture(autouse=True)
def _mock_payspace(settings):
    settings.PAYSPACE = {**settings.PAYSPACE, "ENV": "mock", "CLIENT_ID": "", "CLIENT_SECRET": ""}


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def company(db):
    return Company.objects.create(name="Demo Construction")


@pytest.fixture
def other_company(db):
    return Company.objects.create(name="Someone Else")


@pytest.fixture
def bio3g_key(company):
    _, raw = ApiKey.issue(company, "Bio3G", [s.value for s in ApiKey.Scope])
    return raw


@pytest.fixture
def api(bio3g_key):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {bio3g_key}")
    return client
