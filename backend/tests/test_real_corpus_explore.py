"""Real-corpus slow explore test (plan Task 6): indexes the same tiny
workspace as test_real_corpus_query (fixtures shared via
real_corpus_fixtures, helpers reused from the query test), then proves the
Task 2/3 read path against REAL graphrag 3.1.0 parquet output —
entities list projection, knowledge-graph shape (community coloring via
max-level communities, dangling edges dropped), full-row community-report
detail (findings list + full_content), and the q= keyword filter narrowing
the unfiltered total.

Skipped unless GRAPHRAG_API_KEY is set (real LLM endpoint; the key value
must never appear in any output). Cost/time: one index run on the tiny
corpus (~2-3 min, gpt-4o-mini + text-embedding-3-small, well under $1);
the artifact reads themselves are pure DuckDB and free.
"""

import os

import yaml

# Index-once fixtures come from real_corpus_fixtures (runner loop enabled,
# MAX_CONCURRENT_JOBS=1, known-good micro-corpus); poll/upload helpers are
# still shared with the Phase-4 query test.
from real_corpus_fixtures import (
    DOCS,
    pytestmark,  # noqa: F401  (pytest consumes module attribute)
    real_corpus_app,  # noqa: F401  (query_client resolves this dep by name)
    ws_root,  # noqa: F401  (real_corpus_app resolves this dep by name)
)
from real_corpus_fixtures import (
    real_corpus_client as query_client,  # noqa: F401  (pytest fixture: test param shadows)
)

from tests.test_projects import _setup_two_users
from tests.test_real_corpus_query import _job_to_terminal, _upload


async def test_real_corpus_explore_browse_graph_and_filters(query_client):  # noqa: F811  (fixture imported above)
    client = query_client
    admin = await _setup_two_users(client)

    pid = (
        await client.post(
            "/api/projects",
            headers=admin,
            json={"name": "Real Explore Corpus", "input_file_type": "text"},
        )
    ).json()["id"]
    for name, text in DOCS.items():
        await _upload(client, admin, pid, name, text)

    # Env key: value comes from the environment and must never come back.
    r = await client.patch(
        f"/api/projects/{pid}/env",
        headers=admin,
        json={"key": "GRAPHRAG_API_KEY", "value": os.environ["GRAPHRAG_API_KEY"]},
    )
    assert r.status_code == 204
    secret = os.environ["GRAPHRAG_API_KEY"]
    assert secret not in (await client.get(f"/api/projects/{pid}/env", headers=admin)).text

    # Cheap real-endpoint models via the YAML settings editor (graphrag 3.1.0
    # key layout, same as test_real_corpus_query).
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

    # --- index once (standard); the artifact reads below are LLM-free ---
    job = (
        await client.post(
            f"/api/projects/{pid}/jobs", headers=admin, json={"type": "index", "method": "standard"}
        )
    ).json()
    body = await _job_to_terminal(client, admin, job["id"])
    assert body["status"] == "succeeded", body.get("error")

    # --- (a) entities list: 200 envelope, list projection drops description ---
    r = await client.get(
        f"/api/projects/{pid}/artifacts/entities", params={"limit": 10}, headers=admin
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {"rows", "total", "stale"}
    assert data["stale"] is False
    assert data["total"] >= 1 and data["rows"]
    assert "description" not in data["rows"][0]

    # --- (b) graph: real community hierarchy + title-joined edges ---
    r = await client.get(f"/api/projects/{pid}/artifacts/graph", headers=admin)
    assert r.status_code == 200, r.text
    g = r.json()
    assert g["levels"], "real index yields at least one community level"
    assert len(g["nodes"]) >= 5
    assert all(
        n["community"] is None
        or isinstance(n["community"], int)
        and not isinstance(n["community"], bool)
        for n in g["nodes"]
    ), "community comes from parquet ints or None"
    titles = {n["title"] for n in g["nodes"]}
    assert g["edges"], "the corpus's docs are about the same machine — edges exist"
    assert all(e["source"] in titles and e["target"] in titles for e in g["edges"]), (
        "every edge endpoint must be a real entity title (dangling dropped)"
    )

    # --- (c) community-report full row: list-typed findings survive _clean ---
    r = await client.get(f"/api/projects/{pid}/artifacts/community_reports/0", headers=admin)
    assert r.status_code == 200, r.text
    row = r.json()["row"]
    assert isinstance(row["findings"], list)
    assert isinstance(row["full_content"], str) and row["full_content"].strip()

    # --- (d) q= keyword filter narrows the unfiltered total ---
    needle = data["rows"][0]["title"].split()[0]
    r = await client.get(
        f"/api/projects/{pid}/artifacts/entities", params={"q": needle}, headers=admin
    )
    assert r.status_code == 200, r.text
    filtered = r.json()
    assert 1 <= filtered["total"] < data["total"], (
        f"q={needle!r} must return a strict subset ({filtered['total']} vs {data['total']})"
    )
    assert secret not in r.text
