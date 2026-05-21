import time

import httpx
import pytest

MOCK_IDP_URL = "http://localhost:18080"
APISIX_URL = "http://localhost:19080"
TENANT = "it-tenant"


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires external gateway stack")


def pytest_collection_modifyitems(config, items):
    """Skip all integration tests when the stack is not reachable."""
    try:
        httpx.get(f"{MOCK_IDP_URL}/healthz", timeout=3.0).raise_for_status()
    except Exception:
        skip = pytest.mark.skip(reason="mock-idp not reachable — run docker compose up first")
        for item in items:
            item.add_marker(skip)
        return

    try:
        httpx.get(f"{APISIX_URL}/alpha/get", timeout=3.0)
    except Exception:
        skip = pytest.mark.skip(reason="APISIX not reachable — run docker compose up first")
        for item in items:
            item.add_marker(skip)


def _password_token(resource: str) -> str:
    r = httpx.post(
        f"{MOCK_IDP_URL}/{TENANT}/token",
        data={"grant_type": "password", "username": "it-user", "password": "it-user-pw", "resource": resource},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _cc_token(client_id: str, secret: str, resource: str) -> str:
    r = httpx.post(
        f"{MOCK_IDP_URL}/{TENANT}/token",
        data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": secret, "resource": resource},
    )
    r.raise_for_status()
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def alpha_user_token():
    return _password_token("api://alpha-api")


@pytest.fixture(scope="session")
def beta_user_token():
    return _password_token("api://beta-api")


@pytest.fixture(scope="session")
def alpha_cc_token():
    return _cc_token("aaaaaaaa-aaaa-aaaa-aaaa-111111111111", "alpha-secret", "api://alpha-api")


@pytest.fixture(scope="session")
def beta_cc_token():
    return _cc_token("bbbbbbbb-bbbb-bbbb-bbbb-222222222222", "beta-secret", "api://beta-api")


@pytest.fixture(scope="session")
def expired_token():
    """Issues a 1-second token then waits for it to expire."""
    tok = _cc_token("cccccccc-cccc-cccc-cccc-333333333333", "short-secret", "api://alpha-api")
    time.sleep(2)
    return tok
