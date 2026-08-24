"""Route → error code assertions (i18n spec §4.2), one place for all
routes. detail strings are pinned elsewhere; here we pin the code field.
Query-route entries re-wire the test_query_api seams locally (config stub,
fake adapter/cache) so this file stays lint-clean without fixture imports."""

import pytest
from test_query_api import FakeAdapter, FakeCache, _post, _viewer_setup

from graphrag_ui.config import get_settings
from graphrag_ui.services import query as query_service
from graphrag_ui.services.rate_limit import reset_rate_limiter


@pytest.fixture
def _seams(monkeypatch):
    """Same contract as test_query_api._seams: stub config load, keep the
    settings/limiter singletons from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    reset_rate_limiter()
    yield
    get_settings.cache_clear()
    reset_rate_limiter()


@pytest.fixture
def fake_adapter(monkeypatch):
    adapter = FakeAdapter()
    monkeypatch.setattr(query_service, "GraphragSearchAdapter", lambda: adapter)
    return adapter


@pytest.fixture
def fake_cache(monkeypatch):
    cache = FakeCache()
    monkeypatch.setattr(query_service, "get_frame_cache", lambda: cache)
    return cache


async def test_login_failure_carries_code(client):
    r = await client.post("/api/auth/login",
                          json={"email": "admin@test.local", "password": "wrong"})
    assert r.status_code == 401
    assert r.json()["code"] == "auth_invalid_credentials"
    assert r.json()["detail"] == "invalid email or password"


async def test_must_change_guard_carries_code(client):
    # Fresh bootstrap admin starts must_change_password=True; the global
    # guard 403s any /api path outside the allowlist (main.py §4.4 exit).
    # The guard only fires when Authorization starts with "Bearer " —
    # a bare request would 401 in get_current_user instead.
    login = await client.post(
        "/api/auth/login",
        json={"email": "admin@test.local", "password": "admin-pass-123"})
    token = login.json()["access_token"]
    r = await client.get("/api/projects",
                         headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
    body = r.json()
    assert body["detail"] == "password change required"
    assert body["code"] == "auth_must_change_password"


async def test_query_not_indexed_carries_code(client, app, _seams, fake_adapter):
    # real frame cache + empty workspace: no output/*.parquet
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 409
    assert r.json()["code"] == "not_indexed"


async def test_query_config_failed_carries_code(client, app, _seams, fake_adapter,
                                                fake_cache, monkeypatch):
    from graphrag_ui.adapters.graphrag_search import ConfigLoadError

    def boom(root):
        raise ConfigLoadError("bad settings.yaml: secret missing")

    monkeypatch.setattr(query_service, "load_config", boom)
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 500
    assert r.json()["code"] == "query_config_failed"


async def test_query_failed_carries_code(client, app, _seams, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    fake_adapter.error = RuntimeError("OPENAI key invalid: sk-leaked-123")
    r = await _post(client, pid, alice)
    assert r.status_code == 502
    assert r.json()["code"] == "query_failed"


async def test_query_rate_limited_carries_code(client, app, _seams, fake_adapter,
                                               fake_cache, monkeypatch):
    monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    reset_rate_limiter()
    pid, alice, _, _ = await _viewer_setup(client, app)
    assert (await _post(client, pid, alice)).status_code == 200
    assert (await _post(client, pid, alice)).status_code == 200
    r = await _post(client, pid, alice)
    assert r.status_code == 429
    assert r.json()["code"] == "query_rate_limited"
