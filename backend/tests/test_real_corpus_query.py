"""Real-corpus slow query test (plan Task 6): indexes a tiny workspace once
through the jobs API (standard, gpt-4o-mini), then drives the query pipeline
against the real LLM endpoint — POST basic with joined citations, local via
the SSE stream (?token=), local POST answer-only, and finally the
per-(user, project) rate limiter (limit 2 → third call 429, done last so
the shrunken bucket cannot poison the phases above).

Skipped unless GRAPHRAG_API_KEY is set (real LLM endpoint; the key value
must never appear in any output). Cost/time on the tiny corpus (2026-08-22,
gpt-4o-mini + text-embedding-3-small): index ~2-3 min + one LLM call per
query (~20 s each), well under $1.
"""

import asyncio
import json
import os
import shutil
import time

import pytest
import yaml
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.api import auth_routes
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from graphrag_ui.main import create_app
from graphrag_ui.services.rate_limit import reset_rate_limiter
from tests.test_projects import _setup_two_users

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("GRAPHRAG_API_KEY"),
                       reason="needs real LLM key (GRAPHRAG_API_KEY)"),
]

QUESTION = "Who worked on the Analytical Engine?"

# Factual micro-corpus, 2-3 sentences per document: enough text for the
# standard pipeline's entity extraction to find something on every doc, and
# a question basic search can answer from the text units.
DOCS = {
    "babbage.txt": (
        "Charles Babbage, an English mathematician and inventor, designed the "
        "Analytical Engine between 1834 and 1846. He was Lucasian Professor of "
        "Mathematics at Cambridge from 1828 to 1839. Babbage funded much of the "
        "Engine's development from his own fortune after the British government "
        "withdrew its support."),
    "lovelace.txt": (
        "Ada Lovelace, daughter of the poet Lord Byron, translated Menabrea's "
        "memoir on the Analytical Engine from French and appended her Notes, "
        "published in 1843. Her Note G described an algorithm for computing "
        "Bernoulli numbers, often considered the first published computer "
        "program. She worked closely with Charles Babbage on the Engine."),
    "engine.txt": (
        "The Analytical Engine was a proposed mechanical general-purpose "
        "computer designed around an arithmetic mill, a store for one thousand "
        "numbers, and punch-card control flow borrowed from the Jacquard loom. "
        "It was never completed during Babbage's lifetime, yet its architecture "
        "anticipated the modern CPU."),
}


@pytest.fixture
def ws_root(tmp_path):
    """Workspace root shared with the app; removed afterwards — the corpus
    output/cache artifacts are the test's biggest footprint on disk."""
    root = tmp_path / "ws"
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
async def query_app(clean_db, monkeypatch, ws_root):
    """conftest's app fixture disables the runner loop (MAX_CONCURRENT_JOBS=0)
    so queued jobs are never auto-executed; this variant enables it with cap 1
    so POSTing the index job actually runs it (test_real_corpus_jobs pattern)."""
    monkeypatch.setenv("WORKSPACES_DIR", str(ws_root))
    monkeypatch.setenv("BOOTSTRAP_ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("BOOTSTRAP_ADMIN_PASSWORD", "admin-pass-123")
    monkeypatch.setenv("JWT_SECRET", "test-jwt-secret-0123456789abcdef0123456789abcd")
    monkeypatch.setenv("MAX_CONCURRENT_JOBS", "1")
    get_settings.cache_clear()
    await reset_engine()          # env changed → shared engine must be rebuilt
    auth_routes._LOGIN_FAILURES.clear()
    return create_app()


@pytest.fixture
async def query_client(query_app):
    async with (
        LifespanManager(query_app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()


async def _job_to_terminal(client, headers, job_id, timeout_s=600):
    """Poll GET /api/jobs/{id} every 2 s until terminal (no RSS sampling —
    the memory-budget row is test_real_corpus_jobs' scope, not Task 6's)."""
    deadline = time.monotonic() + timeout_s
    while True:
        body = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
        if body["status"] in TERMINAL_STATUSES:
            return body
        assert time.monotonic() < deadline, (
            f"job {job_id} not terminal after {timeout_s}s (last: {body['status']})")
        await asyncio.sleep(2)


async def _upload(client, headers, pid, name, text):
    r = await client.post(f"/api/projects/{pid}/files", headers=headers,
                          files={"file": (name, text.encode(), "text/plain")})
    assert r.status_code == 201, r.text


def _sse_payload(stream_text: str, event: str):
    """JSON-decode the data line following `event: <event>` in an SSE body."""
    marker = f"event: {event}\n"
    idx = stream_text.find(marker)
    assert idx >= 0, f"no {event!r} event in stream"
    data_line = stream_text[idx + len(marker):].split("\n", 1)[0]
    return json.loads(data_line[len("data: "):])


TIMING_KEYS = {"frames_ms", "search_ms", "citations_ms", "total_ms"}


async def test_real_corpus_query_basic_post_local_stream_rate_limit(
        query_client, ws_root, monkeypatch):
    client = query_client
    admin = await _setup_two_users(client)
    # Fresh access token for the SSE ?token= path (helpers return only headers).
    token = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-new-1",
    })).json()["access_token"]

    # Real init: graphrag CLI actually forks here (~7 s).
    pid = (await client.post("/api/projects", headers=admin,
                             json={"name": "Real Query Corpus",
                                   "input_file_type": "text"})).json()["id"]
    ws = (ws_root / pid).resolve()
    assert (ws / "settings.yaml").is_file() and (ws / "input").is_dir()

    for name, text in DOCS.items():
        await _upload(client, admin, pid, name, text)

    # Env key: value comes from the environment and must never come back.
    r = await client.patch(f"/api/projects/{pid}/env", headers=admin, json={
        "key": "GRAPHRAG_API_KEY", "value": os.environ["GRAPHRAG_API_KEY"]})
    assert r.status_code == 204
    secret = os.environ["GRAPHRAG_API_KEY"]
    assert secret not in (await client.get(f"/api/projects/{pid}/env",
                                           headers=admin)).text

    # Cheap real-endpoint models via the YAML settings editor (graphrag 3.1.0
    # key layout, same as test_real_corpus_jobs).
    got = (await client.get(f"/api/projects/{pid}/settings", headers=admin)).json()
    cfg = yaml.safe_load(got["content"])
    cfg["completion_models"]["default_completion_model"]["model"] = "gpt-4o-mini"
    cfg["embedding_models"]["default_embedding_model"]["model"] = "text-embedding-3-small"
    r = await client.put(f"/api/projects/{pid}/settings", headers=admin, json={
        "content": yaml.safe_dump(cfg, sort_keys=False),
        "expected_hash": got["content_hash"]})
    assert r.status_code == 200, r.text

    # --- index (standard) through the enabled runner loop ---
    job = (await client.post(f"/api/projects/{pid}/jobs", headers=admin,
                             json={"type": "index", "method": "standard"})).json()
    body = await _job_to_terminal(client, admin, job["id"])
    assert body["status"] == "succeeded", body.get("error")
    assert (body["stats"] or {}).get("num_documents") == 3

    # --- (a) basic POST: citations join Sources markers → text_units text ---
    r = await client.post(f"/api/projects/{pid}/query", headers=admin,
                          json={"method": "basic", "query": QUESTION})
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["answer"].strip()
    assert first["citations"], "basic answer should carry [Data: Sources (n)] markers"
    entries = [e for c in first["citations"] for e in c["entries"]]
    assert entries and all(e["text"] is not None for e in entries), \
        "every marker id must join text_units text via sources aliasing"
    assert set(first["timings"]) == TIMING_KEYS
    assert all(v > 0 for v in first["timings"].values())
    assert secret not in r.text

    # --- (b) local via SSE stream with ?token= (EventSource shape) ---
    sse = await client.get(f"/api/projects/{pid}/query/stream", params={
        "method": "local", "query": QUESTION, "response_type": "multiple paragraphs",
        "token": token,
    })
    assert sse.status_code == 200
    assert sse.text.count("event: chunk") >= 1
    stream_citations = _sse_payload(sse.text, "citations")
    assert stream_citations, "local stream should end with a citations event"
    # Stream joins against the cached PARQUET frames: hash-string ids +
    # human_readable_id ints and the reports↔community_reports alias must
    # all hold — the pre-fix bug nulled EVERY entry (empty maps), so
    # requiring one resolved text catches it. gpt-4o-mini also sometimes
    # cites ids that exist in no frame (reproduced: "[Data: Reports (0, 4)]"
    # with only reports 0-1 indexed) — those entries legitimately resolve
    # text null, making all()-non-null unsatisfiable on this phase.
    stream_entries = [e for c in stream_citations for e in c["entries"]]
    assert stream_entries and any(e["text"] is not None for e in stream_entries), \
        "stream markers must join parquet frames (hrid ids + reports alias)"
    done = _sse_payload(sse.text, "done")
    assert set(done) == TIMING_KEYS
    assert all(v > 0 for v in done.values())
    assert secret not in sse.text

    # --- (c) local POST: answer only (local markers are optional) ---
    r = await client.post(f"/api/projects/{pid}/query", headers=admin,
                          json={"method": "local", "query": QUESTION})
    assert r.status_code == 200, r.text
    assert r.json()["answer"].strip()

    # --- (d) rate limit LAST: shrunken bucket (limit 2) in a fresh settings
    # scope; reset pairs the limiter singleton with the settings cache clear.
    monkeypatch.setenv("QUERY_RATE_LIMIT_PER_HOUR", "2")
    get_settings.cache_clear()
    reset_rate_limiter()
    for _ in range(2):
        r = await client.post(f"/api/projects/{pid}/query", headers=admin,
                              json={"method": "basic", "query": QUESTION})
        assert r.status_code == 200, r.text
    r = await client.post(f"/api/projects/{pid}/query", headers=admin,
                          json={"method": "basic", "query": QUESTION})
    assert r.status_code == 429
    assert r.json()["detail"] == "查詢過於頻繁,請稍後再試"
