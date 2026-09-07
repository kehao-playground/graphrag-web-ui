"""Slow upstream-pinning test for the title-recovery rule (spec 6.3).

The §6.3 rule is read off graphrag 3.1.0 source: text readers title every
document with the file's basename (text.py:38), structured readers append
" (N)" per yielded row with N counting from 0 (structured_file_reader.py:
49-53), and input.title_column switches titles to arbitrary row data
(never set by us, reachable via the hand-edit settings editor). Source is
not behavior — this module pins all of it against the real CLI.

Fours tiny workspaces: text, multi-row CSV, multi-row JSON, and a
title_column project. The first three get a real standard index so
documents.parquet exists; the fourth only needs its settings.yaml.

Skipped unless GRAPHRAG_API_KEY is set (real LLM endpoint; the key value
must never appear in any output). Cost/time: three tiny standard indexes,
~2-3 min each worst case (gpt-4o-mini + text-embedding-3-small).
"""

import asyncio
import os
import time

import yaml
from real_corpus_fixtures import (
    pytestmark,  # noqa: F401  (pytest consumes module attribute)
    ws_root,  # used as a value by the workspace helpers below
)
from real_corpus_fixtures import (
    real_corpus_client as titles_client,  # noqa: F401  (guard's canonical binding)
)

from graphrag_ui.adapters.artifacts import read_document_titles
from graphrag_ui.domain.artifacts import recover_filenames, title_column_configured
from graphrag_ui.domain.jobs import TERMINAL_STATUSES
from tests.test_projects import _setup_two_users

# Same shape as the other real-corpus modules: factual micro-texts, 2-3
# sentences each, enough for standard entity extraction to finish cleanly.
TEXT_DOCS = {
    "meridian.txt": (
        "The prime meridian at Greenwich was adopted internationally in 1884 "
        "after a conference in Washington DC. Before that, most nations ran "
        "their own zero longitude through their capital. Satellite geodesy "
        "later shifted the true zero line about a hundred meters east."
    ),
    "basalt.txt": (
        "Basalt is the most common rock in Earth's crust ocean basins and "
        "erupts at mid-ocean ridges. It cools quickly at the surface giving "
        "a fine grain, while the same magma cooled slowly forms gabbro. "
        "Flood basalt provinces cover areas of hundreds of thousands of km2."
    ),
    "quorum.txt": (
        "A quorum is the minimum number of members whose presence makes a "
        "deliberative assembly's decisions valid. Many legislatures set it at "
        "a simple majority, while boards often require a fixed fraction. "
        "Without quorum, votes taken are typically void rather than failed."
    ),
}

# Structured corpora: one file, three rows each -> titles "name (0..2)".
CSV_BODY = (
    "text\n"
    "The Vega telescope in Chile maps the southern sky at infrared wavelengths\n"
    "Its mirror is segmented and actively cooled to reduce thermal noise\n"
    "First light was achieved after a decade of construction in the Atacama\n"
)

JSON_BODY = (
    '[{"text": "Container ships measure capacity in twenty-foot equivalent units"}\n'
    ',{"text": "The largest vessels exceed twenty thousand of those units"}\n'
    ',{"text": "Beam width limits which ports and canals such ships can use"}]\n'
)


async def _upload(client, headers, pid, name: str, text: str):
    r = await client.post(
        f"/api/projects/{pid}/files",
        headers=headers,
        files={"file": (name, text.encode(), "text/plain")},
    )
    assert r.status_code == 201, r.text


async def _run_to_terminal(client, headers, job_id, timeout_s=900):
    """Poll GET /api/jobs/{id} every 2 s until terminal (jobs-module helper,
    minus the RSS sampling this test does not need)."""
    deadline = time.monotonic() + timeout_s
    while True:
        body = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()
        if body["status"] in TERMINAL_STATUSES:
            return body
        assert time.monotonic() < deadline, (
            f"job {job_id} not terminal after {timeout_s}s (last: {body['status']})"
        )
        await asyncio.sleep(2)


async def _indexed_workspace(client, admin, name: str, input_file_type: str, files: dict):
    """Create a project (real graphrag init forks), upload `files`, set the
    env key + cheap real-endpoint models, run a standard index to terminal.
    Returns the workspace path."""
    pid = (
        await client.post(
            "/api/projects", headers=admin, json={"name": name, "input_file_type": input_file_type}
        )
    ).json()["id"]
    ws = (ws_root / pid).resolve()
    for fname, text in files.items():
        await _upload(client, admin, pid, fname, text)
    r = await client.patch(
        f"/api/projects/{pid}/env",
        headers=admin,
        json={"key": "GRAPHRAG_API_KEY", "value": os.environ["GRAPHRAG_API_KEY"]},
    )
    assert r.status_code == 204, r.text
    # Cheap real-endpoint models via the YAML settings editor (same key
    # layout as the jobs slow test).
    got = (await client.get(f"/api/projects/{pid}/settings", headers=admin)).json()
    cfg = yaml.safe_load(got["content"])
    cfg["completion_models"]["default_completion_model"]["model"] = "gpt-4o-mini"
    cfg["embedding_models"]["default_embedding_model"]["model"] = "text-embedding-3-small"
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=admin,
        json={
            "content": yaml.safe_dump(cfg, sort_keys=False),
            "expected_hash": got["content_hash"],
        },
    )
    assert r.status_code == 200, r.text
    job = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=admin, json={"type": "index", "method": "standard"}
        )
    ).json()
    body = await _run_to_terminal(client, admin, job["id"])
    assert body["status"] == "succeeded", body.get("error")
    return ws


async def test_title_recovery_pinned_against_real_cli(titles_client, ws_root):  # noqa: F811  (fixtures imported above)
    client = titles_client
    admin = await _setup_two_users(client)

    # --- text: titles are the bare basenames (text.py:38) ---
    ws = await _indexed_workspace(client, admin, "Titles text", "text", TEXT_DOCS)
    titles = read_document_titles(ws)
    assert titles is not None
    assert sorted(titles) == sorted(TEXT_DOCS)
    assert recover_filenames(titles, frozenset(TEXT_DOCS)) == frozenset(TEXT_DOCS)

    # --- csv: one file, three rows -> "rows.csv (0..2)", which the row-
    #     suffix rule strips back to the filename ---
    ws = await _indexed_workspace(client, admin, "Titles csv", "csv", {"rows.csv": CSV_BODY})
    titles = read_document_titles(ws)
    assert titles is not None
    assert sorted(titles) == ["rows.csv (0)", "rows.csv (1)", "rows.csv (2)"]
    assert recover_filenames(titles, frozenset({"rows.csv"})) == frozenset({"rows.csv"})

    # --- json: an array of three objects takes the same suffixed shape ---
    ws = await _indexed_workspace(client, admin, "Titles json", "json", {"rows.json": JSON_BODY})
    titles = read_document_titles(ws)
    assert titles is not None
    assert sorted(titles) == ["rows.json (0)", "rows.json (1)", "rows.json (2)"]
    assert recover_filenames(titles, frozenset({"rows.json"})) == frozenset({"rows.json"})

    # --- title_column: a hand-edited settings.yaml fires rule 1; no index
    #     needed, the verdict is about the workspace config, not the output ---
    pid = (
        await client.post(
            "/api/projects", headers=admin, json={"name": "Titles column", "input_file_type": "csv"}
        )
    ).json()["id"]
    ws = (ws_root / pid).resolve()
    got = (await client.get(f"/api/projects/{pid}/settings", headers=admin)).json()
    cfg = yaml.safe_load(got["content"])
    cfg.setdefault("input", {})["title_column"] = "title"
    r = await client.put(
        f"/api/projects/{pid}/settings",
        headers=admin,
        json={
            "content": yaml.safe_dump(cfg, sort_keys=False),
            "expected_hash": got["content_hash"],
        },
    )
    assert r.status_code == 200, r.text
    assert title_column_configured(yaml.safe_load((ws / "settings.yaml").read_text()))
