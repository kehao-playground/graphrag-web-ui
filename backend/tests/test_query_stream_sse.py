"""Task 4: SSE streaming query — GET /api/projects/{pid}/query/stream.

FakeStreamAdapter/FakeCache are patched at the service seams; the SSE event
sequence (chunk* → citations → done), the ?token= auth fallback with its
must-change gate, pre-stream JSON errors (429/409/502) and the mid-stream
error event exercise the real route + service + domain citation parser.
SSE events never wrap pre-stream failures: those must be plain JSON HTTP
errors before the 200/stream starts."""

import json

import pandas as pd
import pytest

from graphrag_ui.adapters.workspace import FakeInitializer
from graphrag_ui.api.projects_routes import get_initializer
from graphrag_ui.config import get_settings
from graphrag_ui.services import query as query_service
from graphrag_ui.services.rate_limit import reset_rate_limiter

CHUNKS = ["The ", "Analytical ", "Engine [Data: Sources (2)]."]
SOURCES = pd.DataFrame({"id": [1, 2], "text": ["text one", "text two"]})


class FakeStreamAdapter:
    # Set to N to make the underlying generator raise after N chunks
    # (mid-stream LLM failure); None = deliver all chunks.
    fail_after: int | None = None

    def stream(self, method, config, frames, query, response_type):
        async def gen():
            for i, chunk in enumerate(CHUNKS):
                if self.fail_after is not None and i >= self.fail_after:
                    raise RuntimeError("LLM exploded mid-stream")
                yield chunk

        return gen()


class FakeCache:
    def __init__(self):
        self.tables: list[str] = []

    async def get(self, root, table):
        self.tables.append(table)
        return SOURCES


@pytest.fixture(autouse=True)
def _seams(monkeypatch):
    """Stub config load (empty test workspaces have no settings.yaml); keep
    settings/limiter singletons from leaking across tests."""
    monkeypatch.setattr(query_service, "load_config", lambda root: object())
    reset_rate_limiter()
    yield
    get_settings.cache_clear()
    reset_rate_limiter()


@pytest.fixture
def fake_adapter(monkeypatch):
    adapter = FakeStreamAdapter()
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
    """所有新帳號(含 bootstrap admin)must_change_password=True — 換完密碼才可用。"""
    hdr = await _login(client, email, initial_pw)
    await client.post(
        "/api/auth/change-password",
        headers=hdr,
        json={"current_password": initial_pw, "new_password": new_pw},
    )
    return await _login(client, email, new_pw)


async def _setup_users(client, app):
    """admin + alice(owner)+ bob(稍後加為 viewer)+ carol(非成員)。"""
    app.dependency_overrides[get_initializer] = FakeInitializer
    admin = await _activate(client, "admin@test.local", "admin-pass-123", "admin-new-1")
    for email, pw, name in [
        ("alice@test.local", "alice-pass-1", "Alice"),
        ("bob@test.local", "bob-pass-1234", "Bob"),
        ("carol@test.local", "carol-pass-1", "Carol"),
    ]:
        r = await client.post(
            "/api/admin/users",
            headers=admin,
            json={"email": email, "display_name": name, "password": pw},
        )
        assert r.status_code == 201, r.text
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    bob = await _activate(client, "bob@test.local", "bob-pass-1234", "bob-pass-5678")
    carol = await _activate(client, "carol@test.local", "carol-pass-1", "carol-pass-2")
    return admin, alice, bob, carol


async def _project(client, alice, name="Q4"):
    r = await client.post(
        "/api/projects",
        headers=alice,
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


def _url(pid, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
    return f"/api/projects/{pid}/query/stream?{query}"


def _events(body: str) -> list[tuple[str, object]]:
    """Parse an SSE body into ordered (event, decoded-data) pairs."""
    out: list[tuple[str, object]] = []
    event = None
    for line in body.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: ") :])))
    return out


async def _stream(client, url, headers=None):
    """Collect the full SSE stream body + response headers (200 path)."""
    async with client.stream("GET", url, headers=headers) as resp:
        assert resp.status_code == 200
        headers = dict(resp.headers)
        body = ""
        async for line in resp.aiter_lines():
            body += line + "\n"
    return body, headers


async def test_stream_chunks_then_citations_then_done(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    body, headers = await _stream(client, _url(pid, method="basic", query="What+is+it%3F"), alice)

    events = _events(body)
    # chunk order + exact payloads
    assert events[0] == ("chunk", "The ")
    assert events[1] == ("chunk", "Analytical ")
    assert events[2] == ("chunk", "Engine [Data: Sources (2)].")
    # citations: real domain parser joins Sources (2) against the flattened
    # CACHE frames (basic loads text_units — graphrag's Sources ARE text units)
    assert events[3][0] == "citations"
    assert events[3][1] == [
        {
            "label": "Sources",
            "ids": [2],
            "entries": [{"id": 2, "text": "text two"}],
        }
    ]
    # done: timings dict with the POST-path keys
    assert events[4][0] == "done"
    assert set(events[4][1]) == {"frames_ms", "search_ms", "citations_ms", "total_ms"}
    assert len(events) == 5
    # basic mode loads exactly the text_units table into the stream join
    assert fake_cache.tables == ["text_units"]
    # SSE transport headers (same contract as the job-log stream)
    assert "text/event-stream" in headers["content-type"]
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


async def test_token_query_param_streams(client, app, fake_adapter, fake_cache):
    """?token= auth (EventSource cannot send headers) yields the same stream."""
    pid, _, bob, _ = await _viewer_setup(client, app)
    token = bob["Authorization"].split(" ", 1)[1]
    body, _ = await _stream(client, _url(pid, method="basic", query="q", token=token))
    assert _events(body)[0] == ("chunk", "The ")
    assert any(kind == "done" for kind, _ in _events(body))


async def test_token_query_param_invalid_401(client, app, fake_adapter, fake_cache):
    pid, _, _, _ = await _viewer_setup(client, app)
    r = await client.get(_url(pid, method="basic", query="q", token="not-a-token"))
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/json")


async def test_token_must_change_member_403(client, app, fake_adapter, fake_cache):
    """must-change-password user with a valid ?token= is still 403: the SSE
    helper's gate is the only guard on this path (the global middleware only
    inspects Authorization headers). The user is a project MEMBER viewer, so
    without the gate the stream would be 200 — the test discriminates."""
    admin, alice, _, _ = await _setup_users(client, app)
    pid = await _project(client, alice)
    r = await client.post(
        "/api/admin/users",
        headers=admin,
        json={"email": "mcp-user@example.com", "display_name": "mcp", "password": "mcp-pass-123"},
    )
    assert r.status_code == 201, r.text
    viewer_id = r.json()["id"]
    r = await client.put(
        f"/api/projects/{pid}/members/{viewer_id}", headers=alice, json={"role": "viewer"}
    )
    assert r.status_code in (200, 201), r.text
    r = await client.post(
        "/api/auth/login", json={"email": "mcp-user@example.com", "password": "mcp-pass-123"}
    )
    token = r.json()["access_token"]
    r = await client.get(_url(pid, method="basic", query="q", token=token))
    assert r.status_code == 403
    assert r.json()["detail"] == "password change required"


async def test_rate_limit_third_stream_429_json(client, app, fake_adapter, fake_cache, monkeypatch):
    """429 must arrive as plain JSON BEFORE any SSE byte is written."""
    monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    reset_rate_limiter()
    pid, alice, _, _ = await _viewer_setup(client, app)
    url = _url(pid, method="basic", query="q")
    assert (await client.get(url, headers=alice)).status_code == 200
    assert (await client.get(url, headers=alice)).status_code == 200
    r = await client.get(url, headers=alice)

    assert r.status_code == 429
    assert r.json()["detail"] == "查詢過於頻繁,請稍後再試"
    assert r.headers["content-type"].startswith("application/json")


async def test_unindexed_409_pre_stream_json(client, app, fake_adapter):
    # real frame cache + empty workspace: no output/*.parquet — and the
    # error is JSON, not an SSE error event.
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await client.get(_url(pid, method="basic", query="q"), headers=alice)
    assert r.status_code == 409
    assert r.json()["detail"] == "尚未建立索引,請先執行索引任務"


async def test_mid_stream_adapter_error_event_then_close(client, app, fake_adapter, fake_cache):
    """Adapter raising AFTER the first chunk → SSE error event with the fixed
    zh-TW detail, then the stream closes (no citations/done after it)."""
    pid, alice, _, _ = await _viewer_setup(client, app)
    fake_adapter.fail_after = 2
    body, _ = await _stream(client, _url(pid, method="basic", query="q"), alice)

    events = _events(body)
    assert events == [
        ("chunk", "The "),
        ("chunk", "Analytical "),
        ("error", {"detail": "查詢中斷"}),
    ]


async def test_pre_chunk_adapter_failure_502_json(client, app, fake_adapter, fake_cache):
    """Adapter failing BEFORE any chunk is a pre-stream failure: JSON 502
    with the fixed detail (the cause stays in server logs), not SSE."""
    pid, alice, _, _ = await _viewer_setup(client, app)
    fake_adapter.fail_after = 0
    r = await client.get(_url(pid, method="basic", query="q"), headers=alice)
    assert r.status_code == 502
    assert r.json()["detail"] == "查詢失敗"


async def test_non_member_403(client, app, fake_adapter, fake_cache):
    pid, _, _, carol = await _viewer_setup(client, app)
    r = await client.get(_url(pid, method="basic", query="q"), headers=carol)
    assert r.status_code == 403


async def test_invalid_method_422(client, app, fake_adapter, fake_cache):
    pid, alice, _, _ = await _viewer_setup(client, app)
    r = await client.get(_url(pid, method="nope", query="q"), headers=alice)
    assert r.status_code == 422
