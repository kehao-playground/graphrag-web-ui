"""Task 3: POST /api/projects/{pid}/query — response shape with joined
citations, viewer+ permission, rate limiting (429), unindexed workspace
(409), config-load (500) and adapter failure (502, fixed detail only).
Adapter/frame-cache/config are patched at the service seams; the citation
join and response shape are the real production code under test."""

import pandas as pd
import pytest

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.config import get_settings
from graphrag_ui.services import query as query_service
from graphrag_ui.services.rate_limit import reset_rate_limiter

ANSWER = "首要原因是測試 [Data: Sources (2)]。"
SOURCES = pd.DataFrame({"id": [1, 2], "text": ["文字一", "文字二"]})


class FakeAdapter:
    error: Exception | None = None

    async def search(self, method, config, frames, query, response_type):
        if self.error is not None:
            raise self.error
        return ANSWER, {"sources": SOURCES}


class FakeCache:
    def __init__(self):
        self.tables: list[str] = []

    async def get(self, root, table):
        self.tables.append(table)
        return pd.DataFrame()


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Stub config load everywhere (empty test workspaces have no settings.yaml);
    keep settings/limiter singletons from leaking across tests."""
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


async def _login(client, email, password):
    r = await client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _activate(client, email, initial_pw, new_pw):
    """Every new account (incl. the bootstrap admin) has must_change_password=True — usable only after the change."""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _setup_users(client, app):
    """admin + alice (owner) + bob (added as viewer later) + carol (non-member)."""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    for email, pw, name in [
        ("alice@test.local", "alice-pass-1", "Alice"),
        ("bob@test.local", "bob-pass-1234", "Bob"),
        ("carol@test.local", "carol-pass-1", "Carol"),
    ]:
        r = await client.post(
            "/api/admin/users", headers=admin,
            json={"email": email, "display_name": name, "password": pw},
        )
        assert r.status_code == 201, r.text
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    carol = await _activate(client, "carol@test.local", "carol-pass-1", "carol-pass-2")
    return admin, alice, bob, carol


async def _project(client, alice, name="Q3"):
    r = await client.post(
        "/api/projects", headers=alice,
        json={"name": name, "description": None, "input_file_type": "text"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _add_viewer(client, alice, pid, email):
    users = (await client.get("/api/users", headers=alice)).json()
    vid = next(u["id"] for u in users if u["email"] == email)
    r = await client.put(
        f"/api/projects/{pid}/members/{vid}", headers=alice, json={"role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text


async def _viewer_setup(client, app):
    _, alice, bob, carol = await _setup_users(client, app)
    pid = await _project(client, alice)
    await _add_viewer(client, alice, pid, "bob@test.local")
    return pid, alice, bob, carol


def _post(client, pid, headers, **body):
    payload = {"method": "basic", "query": "測試查詢"}
    payload.update(body)
    return client.post(f"/api/projects/{pid}/query", headers=headers, json=payload)


async def test_owner_response_shape_with_citations(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"answer", "context", "citations", "timings"}
    assert body["answer"] == ANSWER
    # context = frame summaries, never the raw frames
    assert body["context"] == [{"name": "sources", "rows": 2}]
    # marker id 2 joined against the sources frame text
    assert body["citations"] == [
        {"label": "Sources", "ids": [2], "entries": [{"id": 2, "text": "文字二"}]}
    ]
    assert set(body["timings"]) == {"frames_ms", "search_ms", "citations_ms", "total_ms"}
    assert body["timings"]["total_ms"] >= body["timings"]["search_ms"]
    # basic mode loads exactly the text_units table
    assert fake_cache.tables == ["text_units"]


async def test_mode_loads_its_tables(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice, method="local")
    assert r.status_code == 200
    assert fake_cache.tables == [
        "entities", "communities", "community_reports", "text_units", "relationships",
    ]
    r = await _post(client, pid, alice, method="global")
    assert r.status_code == 200
    assert fake_cache.tables[-3:] == ["entities", "communities", "community_reports"]


async def test_viewer_200_non_member_403(client, app, fake_adapter, fake_cache):
    pid, _, bob, carol = await _viewer_setup(client, app)
    assert (await _post(client, pid, bob)).status_code == 200
    r = await _post(client, pid, carol)
    assert r.status_code == 403


async def test_rate_limit_third_post_429(client, app, fake_adapter, fake_cache, monkeypatch):
    monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    reset_rate_limiter()
    pid, alice, bob, _ = await _viewer_setup(client, app)
    assert (await _post(client, pid, alice)).status_code == 200
    assert (await _post(client, pid, alice)).status_code == 200
    r = await _post(client, pid, alice)

    assert r.status_code == 429
    assert r.json()["detail"] == "查詢過於頻繁,請稍後再試"
    # other users are independent buckets
    assert (await _post(client, pid, bob)).status_code == 200



def test_load_config_binds_function_not_submodule():
    """`from graphrag.config import load_config` binds the SUBMODULE (the
    import system shadows the package re-export), which is not callable —
    every fake-adapter test passed while real queries 500'd. Found by the
    real-corpus slow test; guarded here so the fast suite catches regressions."""
    from graphrag_ui.adapters import graphrag_search as gsa

    assert callable(gsa._graphrag_load_config)


def test_adapter_kwargs_match_graphrag_signatures():
    """Per-mode wiring must satisfy the pinned graphrag 3.1.0 callables —
    a KeyError/TypeError here would only surface as 502 at runtime."""
    import inspect

    import pandas as pd

    from graphrag_ui.adapters import graphrag_search as gsa

    frames = {
        name: pd.DataFrame()
        for name in (
            "entities", "communities", "community_reports", "text_units", "relationships",
        )
    }
    config = type("Cfg", (), {})()  # no community_level attr → default applies
    expectations = {
        "basic": {"text_units"},
        "local": {"entities", "communities", "community_reports", "text_units",
                  "relationships", "covariates", "community_level"},
        "drift": {"entities", "communities", "community_reports", "text_units",
                  "relationships", "community_level"},
        "global": {"entities", "communities", "community_reports", "community_level",
                   "dynamic_community_selection"},
    }
    for method, expected_kwargs in expectations.items():
        kwargs = gsa._frames_kwargs(method, config, frames)
        assert set(kwargs) == expected_kwargs, method
        for mode_fns in (gsa._SEARCH_FNS, gsa._STREAM_FNS):
            params = set(inspect.signature(mode_fns[method]).parameters) - {
                "config", "query", "response_type", "callbacks", "verbose"}
            assert params == expected_kwargs, (method, mode_fns[method].__name__)
        if method != "basic":
            assert kwargs["community_level"] == gsa.DEFAULT_COMMUNITY_LEVEL


async def test_graphrag_import_does_not_leak_dotenv(client, app):
    """litellm (via graphrag) load_dotenv() at import time must stay shielded:
    repo-root .env (PROJECT_QUOTA_MB=1 in dev) must not reach os.environ, or
    app settings silently change — the files quota broke exactly this way."""
    import os

    from graphrag_ui.services.files import quota_bytes

    assert "PROJECT_QUOTA_MB" not in os.environ
    assert quota_bytes() == 5000 * 1024 * 1024


async def test_unindexed_workspace_409(client, app, fake_adapter):
    # real frame cache + empty workspace: no output/*.parquet
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 409
    assert r.json()["detail"] == "尚未建立索引,請先執行索引任務"


async def test_config_load_error_500(client, app, fake_adapter, fake_cache, monkeypatch):
    from graphrag_ui.adapters.graphrag_search import ConfigLoadError

    def boom(root):
        raise ConfigLoadError("bad settings.yaml: secret missing")

    monkeypatch.setattr(query_service, "load_config", boom)
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await _post(client, pid, alice)
    assert r.status_code == 500
    assert r.json()["detail"] == "設定載入失敗"


async def test_adapter_error_502_fixed_detail(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    fake_adapter.error = RuntimeError("OPENAI key invalid: sk-leaked-123")
    r = await _post(client, pid, alice)
    assert r.status_code == 502
    # fixed zh-TW message only — internals stay in the server log
    assert r.json()["detail"] == "查詢失敗"


async def test_invalid_body_422(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    assert (await _post(client, pid, alice, method="nope")).status_code == 422
    assert (await _post(client, pid, alice, query="")).status_code == 422


def test_query_errors_share_base():
    """Task 8 (spec A7): QueryError and ExploreReadError share one
    ServicePipelineError base; code/detail contract unchanged, and
    ExploreReadError's historical ``tail`` name still reads through."""
    from graphrag_ui.services.errors import INTERRUPTED_DETAIL, ServicePipelineError
    from graphrag_ui.services.explore import ExploreReadError
    from graphrag_ui.services.query import QueryError

    assert issubclass(QueryError, ServicePipelineError)
    assert issubclass(ExploreReadError, ServicePipelineError)
    e = QueryError("search", "boom")
    assert (e.code, e.detail) == ("search", "boom")
    explore = ExploreReadError("list", "tail text")
    assert (explore.code, explore.detail, explore.tail) == ("list", "tail text", "tail text")
    assert INTERRUPTED_DETAIL == "查詢中斷"
