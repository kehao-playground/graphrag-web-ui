"""Real-corpus slow test (plan Task 8, closes spec §13 rows 1-4): drives the
real graphrag CLI end-to-end through the app — real `graphrag init`, three
tiny .txt uploads, env key, settings model switch (YAML mode), a
runner-executed `index --method standard` job, SSE logs via ?token=, then an
incremental `update --method standard` with delta stats and retention checks.

Skipped unless GRAPHRAG_API_KEY is set (real LLM endpoint; the key value must
never appear in any output). Cost/time on the tiny corpus (2026-08-21,
gpt-4o-mini + text-embedding-3-small): ~2-3 min, well under $1. The fast
method is deliberately not used: it fails on tiny corpora ("Graph Pruning
failed", spec §13 row 4) — standard is the only reliable method here.
"""

import asyncio
import os
import shutil
import subprocess
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

from graphrag_ui.adapters.db import reset_engine
from graphrag_ui.api import auth_routes
from graphrag_ui.config import get_settings
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from graphrag_ui.main import create_app
from tests.test_projects import _setup_two_users

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("GRAPHRAG_API_KEY"),
                       reason="needs real LLM key (GRAPHRAG_API_KEY)"),
]

# Factual micro-corpus, 2-3 sentences per document: enough text for the
# standard pipeline's entity extraction to find something on every doc.
DOCS = {
    "aurora.txt": (
        "The aurora borealis is caused by solar wind particles funneling along "
        "Earth's magnetic field into the polar atmosphere. Oxygen emissions "
        "appear green or red, while nitrogen contributes blue and purple hues. "
        "The display is brightest near the auroral oval around 67 degrees "
        "magnetic latitude."),
    "harbor.txt": (
        "Rotterdam's Europoort is the largest seaport in Europe and handled "
        "roughly 14.5 million containers in 2023. Its deep-dredged access "
        "channels allow fully laden container ships to dock around the clock. "
        "The port generates about one percent of the Dutch GDP."),
    "lithium.txt": (
        "Lithium-ion cells store energy by shuttling lithium ions between a "
        "graphite anode and a metal-oxide cathode. Commercial cells first "
        "shipped in 1991 and now dominate portable electronics and electric "
        "vehicles. Recycling capacity is growing but still lags production."),
}
# Fourth document added before the incremental update (corpus 3 → 4 docs).
EXTRA_DOC = {
    "tidal.txt": (
        "The Bay of Fundy has the highest tidal range on Earth, reaching about "
        "16 meters at spring tide. Twice daily, more water moves through the "
        "bay than the outflow of every river on Earth combined. Tidal power "
        "stations there have generated electricity since 1984."),
}
MUTATED_SENTENCE = (
    "Oxygen emissions appear green, crimson, or deep red depending on "
    "altitude, while nitrogen contributes blue and purple hues.")


@pytest.fixture
def ws_root(tmp_path):
    """Workspace root shared with runner_app; removed afterwards — the corpus
    output/cache artifacts are the test's biggest footprint on disk."""
    root = tmp_path / "ws"
    yield root
    shutil.rmtree(root, ignore_errors=True)


@pytest.fixture
async def runner_app(clean_db, monkeypatch, ws_root):
    """conftest's app fixture disables the runner loop (MAX_CONCURRENT_JOBS=0)
    so queued jobs are never auto-executed; this variant enables it with cap 1
    so POSTing a job actually runs it, exactly like a single-replica deploy."""
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
async def runner_client(runner_app):
    async with (
        LifespanManager(runner_app) as managed,
        AsyncClient(transport=ASGITransport(app=managed.app), base_url="http://t") as c,
    ):
        yield c
    await reset_engine()


def _graphrag_rss_kib(ws_root: Path) -> int:
    """Sum resident memory (KiB) of the live `graphrag` subprocess for this
    workspace (spec §13 row 3 measurement). Job.pid is deliberately never
    recorded (spec demotes it to same-runner internal), so the CLI is found
    by scanning our direct children's command lines for its --root argument.
    Best-effort: a failed probe reads as 0, never fails the test."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss=,command="],
            capture_output=True, text=True, timeout=15, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    me, root = str(os.getpid()), str(ws_root)
    total = 0
    for line in out.splitlines():
        parts = line.strip().split(None, 3)
        if len(parts) == 4 and parts[1] == me and root in parts[3] and "graphrag" in parts[3]:
            total += int(parts[2])
    return total


async def _run_to_terminal(client, headers, job_id, ws_root, timeout_s=600):
    """Poll GET /api/jobs/{id} every 2 s until terminal, sampling the CLI's
    RSS on every poll; returns (final job json, peak RSS KiB)."""
    peak, deadline = 0, time.monotonic() + timeout_s
    while True:
        body = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
        peak = max(peak, _graphrag_rss_kib(ws_root))
        if body["status"] in TERMINAL_STATUSES:
            return body, peak
        assert time.monotonic() < deadline, (
            f"job {job_id} not terminal after {timeout_s}s (last: {body['status']})")
        await asyncio.sleep(2)


async def _upload(client, headers, pid, name: str, text: str):
    r = await client.post(f"/api/projects/{pid}/files", headers=headers,
                          files={"file": (name, text.encode(), "text/plain")})
    assert r.status_code == 201, r.text


async def test_real_corpus_standard_index_then_incremental_update(
        runner_client, ws_root):
    client = runner_client
    admin = await _setup_two_users(client)
    # Fresh access token for the SSE ?token= path (helpers return only headers).
    token = (await client.post("/api/auth/login", json={
        "email": "admin@test.local", "password": "admin-new-1",
    })).json()["access_token"]

    # Real init: graphrag CLI actually forks here (~7 s).
    pid = (await client.post("/api/projects", headers=admin,
                             json={"name": "Real Corpus",
                                   "input_file_type": "text"})).json()["id"]
    ws = (ws_root / pid).resolve()
    assert (ws / "settings.yaml").is_file() and (ws / "input").is_dir()

    for name, text in DOCS.items():
        await _upload(client, admin, pid, name, text)

    # Env key: value comes from the environment and must never come back —
    # assert the secret is absent from every response body below.
    r = await client.patch(f"/api/projects/{pid}/env", headers=admin, json={
        "key": "GRAPHRAG_API_KEY", "value": os.environ["GRAPHRAG_API_KEY"]})
    assert r.status_code == 204
    secret = os.environ["GRAPHRAG_API_KEY"]
    assert secret not in (await client.get(f"/api/projects/{pid}/env",
                                           headers=admin)).text

    # Switch to the cheap real-endpoint models via the YAML settings editor
    # (graphrag 3.1.0 key layout verified against a real `graphrag init`).
    got = (await client.get(f"/api/projects/{pid}/settings", headers=admin)).json()
    cfg = yaml.safe_load(got["content"])
    cfg["completion_models"]["default_completion_model"]["model"] = "gpt-4o-mini"
    cfg["embedding_models"]["default_embedding_model"]["model"] = "text-embedding-3-small"
    r = await client.put(f"/api/projects/{pid}/settings", headers=admin, json={
        "content": yaml.safe_dump(cfg, sort_keys=False),
        "expected_hash": got["content_hash"]})
    assert r.status_code == 200, r.text
    reread = yaml.safe_load((await client.get(f"/api/projects/{pid}/settings",
                                              headers=admin)).json()["content"])
    assert (reread["completion_models"]["default_completion_model"]["model"]
            == "gpt-4o-mini")
    assert (reread["embedding_models"]["default_embedding_model"]["model"]
            == "text-embedding-3-small")
    # $-escaping must survive the round-trip: a bare $ would make the CLI
    # unable to load the workspace (strict Template substitution).
    assert reread["input"]["file_pattern"] == r".*\.(txt|md)$$"

    # --- index (standard) through the enabled runner loop ---
    job = (await client.post(f"/api/projects/{pid}/jobs", headers=admin,
                             json={"type": "index", "method": "standard"})).json()
    body, peak_kib = await _run_to_terminal(client, admin, job["id"], ws)
    assert body["status"] == "succeeded", body.get("error")
    assert (body["stats"] or {}).get("num_documents") == 3
    log = ws / "logs" / "jobs" / f"{job['id']}.log"
    assert log.is_file() and log.stat().st_size > 0
    assert secret not in log.read_text(errors="replace")
    print(f"\n[real-corpus] index peak graphrag RSS: {peak_kib / 1024:.0f} MiB")
    assert (await client.get(f"/api/projects/{pid}/jobs/preflight",
                             headers=admin)).status_code == 200

    # SSE via ?token= (EventSource cannot send headers): log chunks + done.
    sse = await client.get(f"/api/jobs/{job['id']}/logs?token={token}")
    assert sse.status_code == 200
    assert "event: log" in sse.text and "event: done" in sse.text
    assert '"status": "succeeded"' in sse.text
    documents = ws / "output" / "documents.parquet"
    assert pq.read_metadata(documents).num_rows == 3

    # --- incremental update: mutate one doc, add a fourth ---
    await _upload(client, admin, pid, "aurora.txt",
                  DOCS["aurora.txt"].replace(
                      "Oxygen emissions appear green or red, "
                      "while nitrogen contributes blue and purple hues.",
                      MUTATED_SENTENCE))
    for name, text in EXTRA_DOC.items():
        await _upload(client, admin, pid, name, text)
    upd = (await client.post(f"/api/projects/{pid}/jobs", headers=admin,
                             json={"type": "update", "method": "standard"})).json()
    ubody, upeak_kib = await _run_to_terminal(client, admin, upd["id"], ws)
    assert ubody["status"] == "succeeded", ubody.get("error")
    print(f"[real-corpus] update peak graphrag RSS: {upeak_kib / 1024:.0f} MiB")
    # Stats must come from the delta path (update_output/<ts>/delta/stats.json);
    # merge never rewrites output/stats.json (spec §13 row 2), so the presence
    # of update_documents proves the update-specific scan.
    assert (ubody["stats"] or {}).get("update_documents", 0) >= 1
    # Retention: only keep_latest update_output timestamp dirs survive.
    assert len(list((ws / "update_output").iterdir())) \
        <= get_settings().update_output_keep_latest
    # Merge landed the fourth document (spec §13 row 1: 3 → 4).
    assert pq.read_metadata(documents).num_rows == 4
