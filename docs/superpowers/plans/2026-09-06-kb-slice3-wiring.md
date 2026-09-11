# Knowledge-Manager Slice ③ — Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the knowledge manager's loop — a routed project sidebar with a health overview that names the single most useful next action, and a citation that resolves to its source document and opens it at the cited passage.

**Architecture:** `source_name` is resolved **while the answer is produced**, for every answer, against the frames that produced it — there is no endpoint that resolves a citation id after the fact, because such an endpoint could only ever consult current artifacts. The `documents` read is bracketed by a generation guard whose decisive condition is `projects.artifact_epoch == baseline.artifact_epoch`, so a half-written `output/` withholds links instead of opening the wrong document. `/health` turns the recorded evidence into one ordered action card, and the routed sidebar makes every pane linkable.

**Tech Stack:** FastAPI + pydantic v2, SQLAlchemy 2 async, duckdb over parquet, React 19 + TS + antd 6 + react-router 7 + vitest, openapi-typescript codegen.

**Spec:** `docs/superpowers/specs/2026-09-06-knowledge-manager-ux-design.md` — the spec travels with this plan; executors read both. Section references (§7.4, §7.5, §9.3) point at the spec.

**Depends on:** slices ① and ②. From slice ① it needs `index_snapshots` (`baseline_row`, `baseline_entries`, `title_recovery`, `attributable_titles`, `artifact_epoch`), `projects.baseline_snapshot_id` / `projects.artifact_epoch`, `domain/artifacts.recover_filename`, and the reserved `FilePreviewDrawer` `locator` prop. From slice ② it needs `_prepare_query` / `_execute_query`, `test_results.citations`, and the workbench. If either is missing, stop and say so.

## Global Constraints

- **Layering** (AGENTS.md, CI-enforced): `domain/` pure; `services/` no FastAPI imports and no `HTTPException`; `adapters/` owns every duckdb and graphrag touchpoint; `api/` owns routes/schemas/auth.
- **No new environment variables.** `PASSAGE_MAX_BYTES = 4096` and `PREVIEW_WINDOW_BYTES = 64 * 1024` already exist as domain constants from slice ① Task 7.
- **Only `Sources` citations resolve.** `domain/artifacts.py` registers `text_units` with a **singular** `document_id`, so one text unit maps to one document. `Entities`, `Reports`, `Relationships` and `Communities` each summarize many text units spanning many documents; the UI renders them unlinked rather than picking one arbitrarily. This is a product decision, not a limitation to work around.
- **Resolution is best-effort throughout.** A citation id absent from the frame (which `build_citations` already treats as normal), a title that maps to nothing, `unavailable_title_column`, or a guard refusal all render **unlinked**. The answer text is always returned; only the links are withheld.
- **There is no after-the-fact resolution endpoint, and none may be added.** `human_readable_id` is assigned per build — `concat_dataframes` renumbers the delta from the previous maximum and a full `index` restarts the sequence — so `Sources (7)` from last week may name a different text unit today. Resolving a historic citation against current artifacts would not 404; it would open **the wrong document**, silently.
- **`frontend/src/api/types.ts`'s `Citation` is hand-maintained** and `npm run gen:types` will not touch it: the SSE contract has no backend `response_model`. Its `entries[].source_name: string | null` must be edited by hand alongside the schema change.
- **Contract gate**: `openapi.json` + `types.generated.ts` regenerate in the SAME commit as any schema/route change.
- **Every task ends green**: `cd backend && uv run pytest -q -m "not slow"`, `uv run ruff check`, `uv run ruff format --check`, `uv run mypy`. Frontend tasks additionally `cd frontend && npm test && npm run lint && npx tsc -b --noEmit`.
- Comments/docstrings **English only** (CI-enforced); Conventional Commits in English; every new UI string in **both** locales in the same commit; README changes mirror into `docs/zh-TW/` in the same PR.

## File Structure

```
backend/src/graphrag_ui/
  adapters/
    artifacts.py          # Task 1: resolve_document_titles() batched resolver
  services/
    citations.py          # NEW (Task 2): generation guard + source_name enrichment
    query.py              # Task 2: guard brackets the documents read on both paths
    test_runs.py          # Task 2: same helper, persisted with the answer
    files.py              # Task 3: preview_file gains the historic locator path
    health.py             # NEW (Task 4): per-project and batch aggregates
  api/
    files_routes.py       # Task 3: POST preview accepts {result_id, entry_id}
    health_project_routes.py  # NEW (Task 4)
backend/tests/
    test_citation_guard.py    # NEW (Task 2): four barrier cases
    test_citation_source.py   # NEW (Task 2): provenance, batching, removed docs
    test_preview_locator.py   # NEW (Task 3): three bindings, form exhaustiveness
    test_health.py            # Task 4: extend (the file already exists)
frontend/src/
  pages/
    ProjectDetail.tsx     # Task 5: Tabs -> routed Outlet + sidebar
    ProjectOverview.tsx   # NEW (Task 6)
    Projects.tsx          # Task 7: index-health column
  components/
    project/ProjectSidebar.tsx   # NEW (Task 5)
    project/ActionCard.tsx       # NEW (Task 6)
    tests/AnswerView.tsx         # Task 7: clickable Sources citations
  App.tsx                 # Task 5: nested routes
  i18n/locales/{zh-TW,en-US}.ts  # Tasks 5-7
```

---

### Task 1: The batched document resolver

**Files:**
- Modify: `backend/src/graphrag_ui/adapters/artifacts.py`
- Test: `backend/tests/test_adapters_artifacts.py` (extend)

**Interfaces:**
- Consumes: `output/documents.parquet`.
- Produces: `def resolve_document_titles(root: Path, document_ids: Collection[str]) -> dict[str, str]` — a set of document ids in, `{document_id: title}` out, **one** duckdb read of `documents.parquet`. Missing ids are absent from the result, never `None`-valued.

**Resolution is not free, and needs its own adapter call.** The query path
loads only the frames in `FrameCache.TABLES` (`frame_cache.py:12`), which
never include `documents`; and `adapters/artifacts.py` reads one registered
table at a time (`artifacts.py:108`), so nothing existing maps a
`document_id` to a `documents.title`.

**Only `documents` is read fresh.** `text_unit_id → document_id` comes from
the `text_units` frame that answered the query, not from a second read —
`text_units` is loaded for every method that can produce `Sources` citations
(`basic`, `local`, `drift`; `global` loads no text units and so cites no
sources). That is why the resolver takes **document** ids rather than
text-unit ids.

- [x] **Step 1: Write the failing resolver tests**

Append to `backend/tests/test_adapters_artifacts.py`:

```python
def test_resolve_document_titles_returns_only_requested_ids(tmp_workspace):
    root = tmp_workspace(documents=[("d1", "a.md"), ("d2", "b.md"), ("d3", "c.md")])
    assert resolve_document_titles(root, {"d1", "d3"}) == {"d1": "a.md", "d3": "c.md"}


def test_unknown_ids_are_absent_not_none(tmp_workspace):
    root = tmp_workspace(documents=[("d1", "a.md")])
    assert resolve_document_titles(root, {"d1", "ghost"}) == {"d1": "a.md"}


def test_empty_id_set_reads_nothing(tmp_workspace, monkeypatch):
    """A question that cites no sources must not open duckdb at all."""
    root = tmp_workspace(documents=[("d1", "a.md")])
    monkeypatch.setattr(
        artifacts_adapter.duckdb, "connect", _explode("no read for an empty id set")
    )
    assert resolve_document_titles(root, set()) == {}


def test_missing_parquet_yields_an_empty_mapping(tmp_path):
    """Best-effort: a missing documents.parquet renders citations unlinked,
    it does not fail the answer."""
    assert resolve_document_titles(tmp_path, {"d1"}) == {}


def test_one_read_for_many_ids(tmp_workspace, monkeypatch):
    root = tmp_workspace(documents=[(f"d{i}", f"f{i}.md") for i in range(50)])
    reads = {"n": 0}
    real = artifacts_adapter.duckdb.connect

    def counting(*a, **kw):
        reads["n"] += 1
        return real(*a, **kw)

    monkeypatch.setattr(artifacts_adapter.duckdb, "connect", counting)
    out = resolve_document_titles(root, {f"d{i}" for i in range(50)})
    assert len(out) == 50 and reads["n"] == 1
```

- [x] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_adapters_artifacts.py -q -k resolve`
Expected: FAIL — `ImportError: cannot import name 'resolve_document_titles'`.

- [x] **Step 3: Implement**

```python
def resolve_document_titles(root: Path, document_ids: Collection[str]) -> dict[str, str]:
    """{document_id: documents.title} for the ids given, in ONE duckdb read.

    The resolver returns TITLES and stops there. Turning a title into a
    filename is the recovery rule of spec 6.3, and that rule must be the one
    that held when the artifacts were built - so the caller applies the
    BASELINE SNAPSHOT's title_recovery, never today's settings.yaml
    (services/citations.py).

    Best-effort: a missing parquet yields {}, which renders citations
    unlinked rather than failing the answer.
    """
    if not document_ids:
        return {}
    path = root / "output" / "documents.parquet"
    if not path.is_file():
        return {}
    ids = list(document_ids)
    placeholders = ", ".join("?" for _ in ids)
    with duckdb.connect(":memory:") as con:
        rows = con.execute(
            f"SELECT id, title FROM read_parquet(?) WHERE id IN ({placeholders})",
            [str(path), *ids],
        ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows if r[1] is not None}
```

- [x] **Step 4-5: Run, gate, commit**

```bash
cd backend && uv run pytest tests/test_adapters_artifacts.py -q
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend/src/graphrag_ui/adapters/artifacts.py backend/tests/test_adapters_artifacts.py
git commit -m "$(cat <<'EOF'
feat(citations): add a batched documents resolver

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 2: `source_name`, resolved with the answer, behind a generation guard

**Files:**
- Create: `backend/src/graphrag_ui/services/citations.py`
- Modify: `backend/src/graphrag_ui/services/query.py`, `backend/src/graphrag_ui/services/test_runs.py`
- Test: `backend/tests/test_citation_guard.py` (new), `backend/tests/test_citation_source.py` (new)

**Interfaces:**
- Consumes: `resolve_document_titles` (Task 1); `index_snapshots.baseline_row` / `baseline_entries` (slice ① Task 4); `Project.artifact_epoch`; `recover_filename` (slice ① Task 2); `build_citations`.
- Produces:
  - `@dataclass(frozen=True) Generation` with `baseline_snapshot_id: uuid.UUID | None`, `artifact_epoch: int`, `active_index_job: bool`.
  - `async read_generation(project_id: uuid.UUID) -> Generation` — its **own session**, no identity-map reuse.
  - `async enrich_sources(citations: list[dict], text_units: pd.DataFrame | None, root: Path, project_id: uuid.UUID, *, g0: Generation, memo: dict[str, str | None]) -> list[dict]` — steps 3-6 of the normative sequence; returns citations whose `Sources` entries carry `source_name: str | None`. `g0` is read by the caller **before** the frame load, which is why it is a parameter rather than something this function reads for itself.
  - `_execute_query`, `stream_query` and `services/test_runs.py` each call `read_generation` before `_prepare_query` and `enrich_sources` in place of the bare `build_citations` tail.

**Why `source_name` travels with the answer.** Deferring it to a click has
the same bug in a shorter window: an ad-hoc query answers `Sources (1)` →
`file-a`, a full index lands, the user clicks, and a later lookup opens
`file-b`. Ad-hoc queries hold no job mutex, so nothing prevents that
interleaving.

**The normative sequence** — for `_execute_query`, `stream_query` and
`services/test_runs.py` alike:

1. **G0** — fresh DB read of `projects.baseline_snapshot_id`,
   `projects.artifact_epoch`, **and** whether an `index`/`update` job is
   `queued`/`running` for the project.
2. Load frames (`_prepare_query`).
3. Take cited `text_unit_id → document_id` from the loaded `text_units`
   frame.
4. Read `documents.parquet` through the batched resolver and complete
   enrichment.
5. **G1** — fresh DB read of the same three facts, **after** step 4.
6. Emit `source_name` only if **all** of: G0 and G1 report the same pointer;
   G0 and G1 report the same epoch; neither reports an active
   `index`/`update`; **and that epoch equals the `artifact_epoch` recorded
   on the baseline snapshot the pointer names**. Otherwise every
   `source_name` is `null`.

G1 must follow the `documents` read, not the frame load: a guard that closed
at step 2 would still let an index start between step 2 and step 4, which is
precisely the interval it exists to cover.

**The fourth condition is the decisive one.** `IndexRunner` spawns graphrag
with `cwd=root` (`index_runner.py:69`): no staging directory, no rollback,
and a non-zero exit only marks the row `failed` (`index_runner.py:114`).
Failed and cancelled jobs promote nothing, so the pointer does not move — a
failed attempt's damage is permanent until the next successful promotion,
and the very next query would see a stable pointer and epoch at both ends
and happily emit links against the wreckage. Comparing the epoch across
G0/G1 detects an attempt *inside* the guarded interval; only
`projects.artifact_epoch == baseline.artifact_epoch` asserts that the
artifacts on disk are the ones this baseline describes.

**The candidate filenames are the baseline's ENTRY names, not
`attributable_titles`.** `attributable_titles` is a *result* of applying the
recovery rule, not an input to it: feeding it back in would make citation
resolution circular with the `skipped` computation that consumes it, and
would couple resolution to whether that computation was available — the
field is empty whenever `title_recovery` is unavailable, so resolution would
silently inherit a narrowing it has no reason to. The entry names are the
primary record and cannot narrow.

- [x] **Step 1: Write the four barrier cases**

Create `backend/tests/test_citation_guard.py`:

```python
"""The generation guard (spec 7.4).

Every case here is a barrier test on purpose. A test that only rebuilds
AFTER the response passes without the guard existing at all, and each of
the first three cases is passed by an implementation that is wrong in a
different way. The fourth is the one a G0/G1-only epoch comparison cannot
catch.
"""


async def test_a_promotion_between_the_frame_load_and_the_documents_read_withholds_links(
    project_indexed, park_before_documents_read
):
    """Case 1. The answer must still be returned - only the links are
    withheld."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _promote_new_baseline(project_indexed)
        parked.release()
        body = await task

    assert body["answer"]
    assert all(
        e["source_name"] is None
        for c in body["citations"] if c["label"] == "Sources"
        for e in c["entries"]
    )


async def test_an_index_starting_in_that_interval_withholds_links(
    project_indexed, park_before_documents_read
):
    """Case 2, which a G1-at-frame-load implementation fails: an index
    starts without promoting a baseline, so the pointer and the epoch at G1
    would both look unchanged to a guard that closed too early. G1 must see
    the ACTIVE JOB and withhold."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _start_index_job(project_indexed)  # queued, never finishes
        parked.release()
        body = await task

    assert _all_source_names_null(body)


async def test_a_failed_index_inside_the_interval_withholds_links(
    project_indexed, park_before_documents_read
):
    """Case 3, the ABA control, which pointer-and-active-job comparison
    alone cannot catch: the index runs to completion AND FAILS, rewriting
    documents.parquet on its way. At G1 the pointer is unmoved and no job is
    active, yet links must still be withheld - only artifact_epoch
    distinguishes it."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(run_query(project_indexed, user, "local", "q"))
        await parked.reached
        await _run_index_to_failure(project_indexed)  # bumps epoch, promotes nothing
        parked.release()
        body = await task

    assert _all_source_names_null(body)


async def test_a_brand_new_query_after_a_failed_index_still_withholds_links(project_indexed):
    """Case 4, which a G0/G1-only epoch comparison cannot catch: let that
    same failed index finish completely, THEN start a brand-new query. G0
    and G1 agree on pointer and epoch and neither sees an active job, yet
    links must still be withheld - only
    projects.artifact_epoch == baseline.artifact_epoch rejects it."""
    await _run_index_to_failure(project_indexed)

    body = await run_query(project_indexed, user, "local", "q")
    assert body["answer"]
    assert _all_source_names_null(body)


async def test_a_successful_full_index_restores_links(project_indexed):
    """The conservatism must be recoverable, in both of the last two cases."""
    await _run_index_to_failure(project_indexed)
    assert _all_source_names_null(await run_query(project_indexed, user, "local", "q"))

    await _run_index_to_success(project_indexed)
    body = await run_query(project_indexed, user, "local", "q")
    assert any(
        e["source_name"] is not None
        for c in body["citations"] if c["label"] == "Sources"
        for e in c["entries"]
    )


async def test_the_streaming_path_is_guarded_identically(
    project_indexed, park_before_documents_read
):
    """The SSE route is the path the UI actually uses; a guard on run_query
    alone would leave the real one open."""
    async with park_before_documents_read() as parked:
        async def drain():
            return [(k, v) async for k, v in stream_query(project_indexed, user, "local", "q")]

        task = asyncio.create_task(drain())
        await parked.reached
        await _run_index_to_failure(project_indexed)
        parked.release()
        events = await task

    citations = next(v for k, v in events if k == "citations")
    assert any(k == "chunk" for k, _ in events)  # the answer still streamed
    assert all(
        e["source_name"] is None
        for c in citations if c["label"] == "Sources"
        for e in c["entries"]
    )


async def test_a_batch_run_is_guarded_identically(
    db_session, run_ready, park_before_documents_read
):
    """services/test_runs.py persists what it resolved, so an unguarded
    batch would write the wrong filename into history permanently."""
    async with park_before_documents_read() as parked:
        task = asyncio.create_task(
            execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
        )
        await parked.reached
        await _run_index_to_failure(run_ready.project_id)
        parked.release()
        await task

    rows = await _results(db_session, run_ready.run_id)
    assert all(r.answer for r in rows)
    assert all(
        e["source_name"] is None
        for r in rows for c in (r.citations or []) if c["label"] == "Sources"
        for e in c["entries"]
    )
```

> `park_before_documents_read` is an async context manager defined at the
> top of this module. It replaces `services.citations.resolve_document_titles`
> with a wrapper that sets `parked.reached`, awaits `parked.release()`, and
> then delegates — so the park point sits exactly between step 3 and step 4
> of the normative sequence, which is the interval the guard exists to
> cover. It restores the original on exit. Parking anywhere else (before the
> frame load, or after G1) tests nothing: an implementation with no guard at
> all would pass.

- [x] **Step 2: Write the source-resolution tests**

Create `backend/tests/test_citation_source.py`:

```python
async def test_stored_run_citations_never_re_resolve(client, run_with_citations):
    """Run A cites Sources (1) -> file-a; rebuild so current Sources (1) ->
    file-b; opening run A's citation must still reach file-a and must NEVER
    reach file-b. This is the negative test that catches a resolver quietly
    falling back to current artifacts."""
    alice, pid, run_id = run_with_citations  # stored source_name == "file-a"
    await _rebuild_so_that_hrid_1_is(pid, "file-b")

    results = (await client.get(f"/api/test-runs/{run_id}/results", headers=alice)).json()
    entry = results["results"][0]["citations"][0]["entries"][0]
    assert entry["source_name"] == "file-a"


async def test_adhoc_citations_do_not_re_resolve_either(client, indexed_project_api):
    """Same shape, different door: answer an ad-hoc query citing
    Sources (1) -> file-a, rebuild so current Sources (1) -> file-b, then
    open the citation from the still-rendered answer. It must reach
    file-a."""
    alice, pid = indexed_project_api
    body = (await client.post(f"/api/projects/{pid}/query", headers=alice,
                              json={"method": "local", "query": "q"})).json()
    name = body["citations"][0]["entries"][0]["source_name"]
    passage = body["citations"][0]["entries"][0]["text"]
    assert name == "file-a"

    await _rebuild_so_that_hrid_1_is(pid, "file-b")
    # The client still holds the payload; nothing re-resolves it.
    r = await client.post(f"/api/projects/{pid}/files/{name}/preview", headers=alice,
                          json={"passage": passage[:100]})
    assert r.status_code == 200 and r.json()["match"] is True


async def test_resolver_provenance_uses_the_baselines_rule_not_todays(client, title_column_project):
    """data.csv indexed under title_column emits the title report.md, the
    project also contains a real report.md, and the setting is later removed
    without a rebuild. Today's rule would confidently attribute data.csv's
    citation to report.md; the baseline's rule must render it UNLINKED."""
    alice, pid = title_column_project
    body = (await client.post(f"/api/projects/{pid}/query", headers=alice,
                              json={"method": "local", "query": "q"})).json()
    assert _all_source_names_null(body)


async def test_citations_into_removed_documents_stay_linkable(client, removed_doc_project):
    """Full-index old.md, delete it, update, then cite the still-indexed
    document: source_name must be old.md. The candidate set is the
    baseline's ENTRY names, which still remember it - a live input/ listing
    would have dropped exactly this name."""
    alice, pid = removed_doc_project
    body = (await client.post(f"/api/projects/{pid}/query", headers=alice,
                              json={"method": "local", "query": "q"})).json()
    names = {e["source_name"] for c in body["citations"] for e in c["entries"]}
    assert "old.md" in names


async def test_only_sources_citations_carry_a_source_name(client, indexed_project_api):
    """Entities, Reports, Relationships and Communities each summarize many
    text units spanning many documents; a single-document link would be a
    lie."""
    alice, pid = indexed_project_api
    body = (await client.post(f"/api/projects/{pid}/query", headers=alice,
                              json={"method": "local", "query": "q"})).json()
    labels = {c["label"] for c in body["citations"]}
    assert labels - {"Sources"}, "fixture must produce a non-Sources citation too"
    for c in body["citations"]:
        if c["label"] != "Sources":
            assert all(e.get("source_name") is None for e in c["entries"])


async def test_one_resolver_call_per_question_querying_only_unmemoed_ids(db_session, run_ready):
    """Call-count contract: at most ONE resolver call per completed
    question, and it queries only ids not already in the run's memo. 200
    questions citing the same five text units issue exactly one call."""
    calls = []
    original = artifacts_adapter.resolve_document_titles

    def recording(root, ids):
        calls.append(set(ids))
        return original(root, ids)

    citations_service.resolve_document_titles = recording
    try:
        await execute_test_run(run_ready.job_id, run_ready.root, cancel_requested=lambda: False)
    finally:
        citations_service.resolve_document_titles = original

    assert len(calls) == 1
    assert len(calls[0]) == 5


async def test_a_global_query_cites_no_sources_and_reads_no_documents(project_indexed, monkeypatch):
    """global loads no text units, so there is nothing to resolve and the
    resolver must not be called at all."""
    monkeypatch.setattr(citations_service, "resolve_document_titles", _explode("no read"))
    await run_query(project_indexed, user, "global", "q")


async def test_a_missing_baseline_renders_unlinked(client, artifacts_without_baseline):
    """When there is no trustworthy baseline, entries stay unlinked; the
    resolver never falls back to inspecting current configuration or
    guessing from input/.

    The fixture is the real case, not a contrived one: a project whose
    output/ was produced before this feature shipped has artifacts and no
    baseline row at all.
    """
    alice, pid = artifacts_without_baseline
    body = (await client.post(f"/api/projects/{pid}/query", headers=alice,
                              json={"method": "local", "query": "q"})).json()
    assert body["answer"]
    assert _all_source_names_null(body)
```

- [x] **Step 3: Run to verify they fail**

Run: `cd backend && uv run pytest tests/test_citation_guard.py tests/test_citation_source.py -q`
Expected: FAIL — `ModuleNotFoundError: graphrag_ui.services.citations`.

- [x] **Step 4: Implement `services/citations.py`**

```python
"""Citation -> source resolution, bracketed by a generation guard (spec 7.4).

A citation id is only meaningful against the artifacts that produced it:
human_readable_id is assigned per build, so Sources (7) from last week may
name a different text unit today. Resolving a historic citation against
current artifacts would not 404 - it would open THE WRONG DOCUMENT,
silently. So source_name is resolved while the answer is produced, for
every answer, and no endpoint resolves a citation id after the fact.

The guard does not stop the race; it detects it and refuses to guess, which
is the only honest option for a path that cannot hold the job mutex. The
answer text is always returned - only the links are withheld.
"""


@dataclass(frozen=True)
class Generation:
    baseline_snapshot_id: uuid.UUID | None
    artifact_epoch: int
    active_index_job: bool


async def read_generation(project_id: uuid.UUID) -> Generation:
    """Fresh read of the three facts, in its OWN session: an identity-map
    reuse would return G0's values at G1 and defeat the whole bracket."""
    async with get_session_factory()() as s:
        row = (await s.execute(
            select(Project.baseline_snapshot_id, Project.artifact_epoch)
            .where(Project.id == project_id)
        )).one()
        active = (await s.execute(
            select(Job.id)
            .where(Job.project_id == project_id,
                   Job.status.in_(("queued", "running")),
                   Job.type.in_(FREEZING_JOB_TYPES))
            .limit(1)
        )).scalar_one_or_none()
    return Generation(row[0], int(row[1]), active is not None)


def _trustworthy(g0: Generation, g1: Generation, baseline_epoch: int | None) -> bool:
    """All four conditions of spec 7.4 step 6.

    The FOURTH is the decisive one. The G0/G1 comparison detects an attempt
    inside the guarded interval; it says nothing about whether the artifacts
    already on disk at G0 came from the baseline. A failed job rewrites
    output/ in place and promotes nothing, so its damage outlives the
    interval and every later query would otherwise pass.
    """
    if baseline_epoch is None or g0.baseline_snapshot_id is None:
        return False
    return (
        g0.baseline_snapshot_id == g1.baseline_snapshot_id
        and g0.artifact_epoch == g1.artifact_epoch
        and not g0.active_index_job
        and not g1.active_index_job
        and g0.artifact_epoch == baseline_epoch
    )
```

`enrich_sources` runs the sequence: caller passes G0 (read before the frame
load); collect the cited `text_unit` hrids from the `Sources` citations;
map them to `document_id` through the **already-loaded** `text_units` frame;
call `resolve_document_titles` for the ids not already in `memo`; apply the
baseline's `title_recovery` and `recover_filename` against the baseline's
**entry names**; read G1; if `_trustworthy(...)` attach the names, else
attach `None` to every `Sources` entry.

`memo` is a per-run `dict[str, str | None]` the batch service threads through
every question, which is what makes the call-count contract testable.

- [x] **Step 5: Wire the three call sites**

`_execute_query` and `stream_query` both read G0 before `_prepare_query`'s
frame load and call `enrich_sources` in place of the bare `build_citations`
tail. `services/test_runs.py` passes its run-scoped memo. The `citations`
payload shape gains `source_name` on `Sources` entries only, so the SSE
`citations` event and the `POST /query` body both carry it.

- [x] **Step 6: Update the hand-maintained frontend `Citation` type**

`frontend/src/api/types.ts`:

```ts
// no backend response_model yet — hand-maintained (spec A5.2)
export interface Citation {
  label: string; ids: number[];
  // source_name is resolved WITH the answer (spec §7.4) and is null when
  // the generation guard withheld links, when the title mapped to nothing,
  // or when the label is not "Sources". `npm run gen:types` will not
  // produce this — the SSE contract has no backend response_model.
  entries: { id: number; text: string | null; source_name: string | null }[];
}
```

- [x] **Step 7: Run, regenerate, gate, commit**

```bash
cd backend && uv run pytest tests/test_citation_guard.py tests/test_citation_source.py tests/test_query_api.py tests/test_query_stream_sse.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
cd ../frontend && npm test && npx tsc -b --noEmit
git add backend frontend/src openapi.json
git commit -m "$(cat <<'EOF'
feat(citations): resolve source_name with the answer behind a generation guard

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 3: The historic preview locator and its three bindings

**Files:**
- Modify: `backend/src/graphrag_ui/services/files.py`, `backend/src/graphrag_ui/api/files_routes.py`
- Test: `backend/tests/test_preview_locator.py` (new file)

**Interfaces:**
- Consumes: `preview_file` (slice ① Task 7); `TestResult`, `TestRun` (slice ② Task 1).
- Produces: `POST /api/projects/{id}/files/{name}/preview` accepts **either** `{"result_id", "entry_id"}` (historic) or `{"passage"}` (ad-hoc). `services/files.resolve_stored_passage(session, project_id, result_id, entry_id, name) -> str` raising `LocatorMismatch` on any binding failure.

**The historic locator carries an authorization boundary, and it is not the
path project.** `result_id` is a client-supplied identifier for a row in
another table; checking `project:view` on the path project says nothing
about which project *that row* belongs to, and a UUID being hard to guess is
not access control. Left unbound, a stored passage becomes a cross-project
read: hold rights on project A, pass a `result_id` from project B, and the
response is B's document text.

Three bindings, and **any mismatch is a 404** — never a 403, which would
confirm the row exists:

1. `test_results.run_id → test_runs.project_id` equals the path project.
2. `entry_id` names an entry of a **`Sources`** citation on that result.
3. That entry's stored `source_name` equals the path `{name}`.

Binding 3 is what stops the confused deputy: without it a caller with
legitimate rights could pair a real `result_id` with any filename and have
the server search a different document for the stored passage.

**Locator forms are exhaustive, not permissive.** `GET` (no body) → the head
window. `POST {"result_id", "entry_id"}` → historic. `POST {"passage"}` →
ad-hoc. Anything else — one half of the historic pair, or `passage` mixed
with either — is **422**; a partially specified locator is a caller bug, and
guessing an interpretation is how the bindings above get bypassed by
accident.

- [x] **Step 1: Write the failing binding tests**

Create `backend/tests/test_preview_locator.py`:

```python
"""Historic preview locator bindings (spec 7.4). Every failure is a 404."""


async def test_historic_locator_opens_the_stored_passage(client, run_with_citations):
    alice, pid, result_id, entry_id = run_with_citations  # source_name == "file-a"
    r = await client.post(
        f"/api/projects/{pid}/files/file-a/preview", headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 200 and r.json()["match"] is True


async def test_a_result_id_from_another_project_is_404(client, two_projects_with_runs):
    """Binding 1. project:view on the PATH project says nothing about which
    project that row belongs to."""
    alice_a, pid_a, result_id_b, entry_id_b = two_projects_with_runs
    r = await client.post(
        f"/api/projects/{pid_a}/files/file-a/preview", headers=alice_a,
        json={"result_id": result_id_b, "entry_id": entry_id_b},
    )
    assert r.status_code == 404
    assert r.json()["code"] != "forbidden"


async def test_an_entry_from_a_different_result_is_404(client, two_results):
    """Binding 2: entry_id must name an entry OF THAT RESULT."""
    alice, pid, result_a, entry_id_of_b = two_results
    r = await client.post(
        f"/api/projects/{pid}/files/file-a/preview", headers=alice,
        json={"result_id": result_a, "entry_id": entry_id_of_b},
    )
    assert r.status_code == 404


async def test_an_entry_of_a_non_sources_citation_is_404(client, run_with_entity_citation):
    """Binding 2, other half: only Sources entries carry a source_name at
    all, so an Entities entry id can never be a valid locator."""
    alice, pid, result_id, entities_entry_id = run_with_entity_citation
    r = await client.post(
        f"/api/projects/{pid}/files/file-a/preview", headers=alice,
        json={"result_id": result_id, "entry_id": entities_entry_id},
    )
    assert r.status_code == 404


async def test_a_valid_result_paired_with_another_filename_is_404(client, run_with_citations):
    """Binding 3, the confused deputy: without it a caller with legitimate
    rights could pair a real result_id with any filename and have the server
    search a different document for the stored passage."""
    alice, pid, result_id, entry_id = run_with_citations  # source_name == "file-a"
    r = await client.post(
        f"/api/projects/{pid}/files/file-b/preview", headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "body",
    [
        {"result_id": "..."},                                # half the pair
        {"entry_id": 1},                                     # the other half
        {"passage": "x", "result_id": "...", "entry_id": 1}, # mixed
        {"passage": "x", "entry_id": 1},                     # mixed
        {},                                                  # neither
    ],
)
async def test_partial_or_mixed_locators_are_422(client, run_with_citations, body):
    alice, pid, result_id, entry_id = run_with_citations
    filled = {
        k: (result_id if k == "result_id" else entry_id if k == "entry_id" else v)
        for k, v in body.items()
    }
    r = await client.post(
        f"/api/projects/{pid}/files/file-a/preview", headers=alice, json=filled
    )
    assert r.status_code == 422


async def test_get_still_returns_the_head_window(client, run_with_citations):
    """The GET form is unchanged by this task."""
    alice, pid, _, _ = run_with_citations
    r = await client.get(f"/api/projects/{pid}/files/file-a/preview", headers=alice)
    assert r.status_code == 200 and r.json()["offset"] == 0 and r.json()["match"] is False


async def test_a_stored_source_name_whose_file_was_deleted_is_a_clean_404(
    client, run_with_citations
):
    """The drawer renders it as disabled rather than 404-ing open - but the
    endpoint's contract is still a 404, and the frontend must not call it."""
    alice, pid, result_id, entry_id = run_with_citations
    await client.delete(f"/api/projects/{pid}/files/file-a", headers=alice)
    r = await client.post(
        f"/api/projects/{pid}/files/file-a/preview", headers=alice,
        json={"result_id": result_id, "entry_id": entry_id},
    )
    assert r.status_code == 404
```

- [x] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_preview_locator.py -q`
Expected: FAIL — 422 on the historic body (slice ① served `{passage}` only).

- [x] **Step 3: Implement**

Replace `PreviewIn` with a discriminated pair validated by a model validator,
keeping `extra="forbid"`:

```python
class PreviewIn(BaseModel):
    """Exactly one locator form. A partially specified locator is a caller
    bug, and guessing an interpretation is how the bindings in
    resolve_stored_passage get bypassed by accident (spec 7.4)."""

    model_config = ConfigDict(extra="forbid")

    result_id: uuid.UUID | None = None
    entry_id: int | None = None
    passage: str | None = None

    @model_validator(mode="after")
    def _exactly_one_form(self) -> "PreviewIn":
        historic = self.result_id is not None and self.entry_id is not None
        half = (self.result_id is None) != (self.entry_id is None)
        adhoc = self.passage is not None
        if half or (historic and adhoc) or not (historic or adhoc):
            raise ValueError("provide either {result_id, entry_id} or {passage}")
        if adhoc:
            if not self.passage:
                raise ValueError("passage must not be empty")
            # The bound is on BYTES: pydantic's string max_length counts
            # characters, so a CJK passage would pass a character check at
            # three times the byte budget.
            if len(self.passage.encode("utf-8")) > PASSAGE_MAX_BYTES:
                raise ValueError(f"passage exceeds {PASSAGE_MAX_BYTES} bytes")
        return self
```

`resolve_stored_passage` runs the three bindings in one query joining
`test_results` to `test_runs`, then walks the stored `citations` JSON for a
`Sources` citation whose entry `id == entry_id`, and compares its
`source_name` to the path name. Every failure raises the **same**
`LocatorMismatch`, and the route maps it to 404 with a single fixed message
— distinguishing them in the response would leak which binding failed.

- [x] **Step 4-6: Run, regenerate, gate, commit**

```bash
cd backend && uv run pytest tests/test_preview_locator.py tests/test_files.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(citations): open a historic citation at its stored passage

The locator verifies three bindings - result to project, entry to result
and Sources, source_name to path name - and any mismatch is a 404, never a
403, which would confirm the row exists.

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 4: Health aggregates

**Files:**
- Create: `backend/src/graphrag_ui/services/health.py`, `backend/src/graphrag_ui/api/health_project_routes.py`
- Modify: `backend/src/graphrag_ui/main.py`
- Test: `backend/tests/test_health.py` (extend)

**Interfaces:**
- Consumes: `list_files` / `IngestCheck` (slice ①), `baseline_row` (slice ①), `count_regressions` (slice ② Task 6).
- Produces:
  - `GET /api/projects/{id}/health` (`project:view`) →
    `{files: {new, modified, indexed, skipped, removed, total}, ingest_check, has_baseline, artifacts_stale, last_index: {job_id, type, finished_at} | null, active_job: {id, type} | null, latest_run: {run_id, set_id, method, index_job_id, ratings: {good, fair, poor, unrated}, regressions} | null}`
  - `GET /api/projects/health?ids=<uuid,uuid,…>` (`project:view`) → the compact subset: `files.new`, `files.modified`, `files.removed`, **`files.skipped`**, **`ingest_check`**, `artifacts_stale`, `has_baseline`, `last_index.finished_at`, filtered to projects the caller can see.

**`ingest_check` and `has_baseline` are reported together** because their
combination carries a fault neither shows alone:
`ingest_check == "unavailable_not_indexed"` **while `has_baseline` is true**
means the project once had output and the file is no longer there — deleted,
or on a volume that did not come back. Its files still report `indexed` from
the baseline, which is why the overview must call it out instead of scoring
the project healthy.

**`skipped` and `ingest_check` are in the batch subset deliberately.** A
project whose only fault is silently dropped documents, or whose output has
gone missing under an existing baseline, is not healthy, and an aggregate
that omitted them would report that it was.

**`regressions` is computed server-side** because the overview must state it
without downloading every result. `artifacts_stale` is
`projects.artifact_epoch != baseline.artifact_epoch` — the state where files
still read `indexed` from the baseline while `output/` holds a failed
attempt's leftovers, and where citation links are switched off.

- [x] **Step 1: Write the failing health tests**

Append to `backend/tests/test_health.py`:

```python
async def test_health_counts_every_state_and_totals_the_union(client, mixed_project):
    alice, pid = mixed_project  # 1 new, 1 modified, 1 indexed, 1 skipped, 1 removed
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["files"] == {
        "new": 1, "modified": 1, "indexed": 1, "skipped": 1, "removed": 1, "total": 5
    }


async def test_missing_output_under_an_existing_baseline_is_reported_as_a_fault(
    client, indexed_project
):
    """The combination carries a fault neither field shows alone."""
    alice, pid = indexed_project
    (ws_path(uuid.UUID(pid)) / "output" / "documents.parquet").unlink()

    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["has_baseline"] is True
    assert body["ingest_check"] == "unavailable_not_indexed"


async def test_artifacts_stale_after_a_failed_index(client, indexed_project):
    alice, pid = indexed_project
    await _run_index_to_failure(pid)

    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["artifacts_stale"] is True

    await _run_index_to_success(pid)
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["artifacts_stale"] is False


async def test_regressions_are_counted_server_side(client, two_rated_runs):
    alice, pid = two_rated_runs  # one lineage went good -> poor
    body = (await client.get(f"/api/projects/{pid}/health", headers=alice)).json()
    assert body["latest_run"]["regressions"] == 1
    assert body["latest_run"]["ratings"] == {"good": 1, "fair": 0, "poor": 1, "unrated": 1}


async def test_batch_health_is_one_round_trip_filtered_to_visible_projects(
    client, three_projects_two_visible
):
    """Without it the list would issue one request per project."""
    viewer, visible_a, visible_b, hidden = three_projects_two_visible
    r = await client.get(
        f"/api/projects/health?ids={visible_a},{visible_b},{hidden}", headers=viewer
    )
    assert r.status_code == 200
    assert set(r.json()["projects"]) == {visible_a, visible_b}


async def test_batch_health_carries_skipped_and_ingest_check(client, mixed_project):
    alice, pid = mixed_project
    body = (await client.get(f"/api/projects/health?ids={pid}", headers=alice)).json()
    entry = body["projects"][pid]
    assert entry["files"]["skipped"] == 1
    assert entry["ingest_check"] == "available"
    assert "artifacts_stale" in entry and "has_baseline" in entry


async def test_batch_health_rejects_an_oversized_id_list(client, alice_headers):
    r = await client.get("/api/projects/health?ids=" + ",".join([str(uuid.uuid4())] * 201),
                         headers=alice_headers)
    assert r.status_code == 422
```

- [x] **Step 2: Run to verify it fails**

Run: `cd backend && uv run pytest tests/test_health.py -q`
Expected: FAIL — 404 on `/api/projects/{id}/health`.

- [x] **Step 3: Implement**

`services/health.py` reuses `list_files` for the per-project counts rather
than reimplementing the enumeration — one source of truth for what `removed`
means. The batch endpoint loops the visible ids with the same function and
projects the compact subset; cap `ids` at 200 entries.

- [x] **Step 4-6: Run, regenerate, gate, commit**

```bash
cd backend && uv run pytest tests/test_health.py -q
uv run python scripts/gen_openapi.py && cd ../frontend && npm run gen:types && cd ../backend
uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
git add backend frontend/src/api/types.generated.ts openapi.json
git commit -m "$(cat <<'EOF'
feat(health): report knowledge-base health per project and in bulk

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 5: Frontend — the routed second-level sidebar

**Files:**
- Create: `frontend/src/components/project/ProjectSidebar.tsx`
- Modify: `frontend/src/App.tsx`, `frontend/src/pages/ProjectDetail.tsx`, both locales
- Test: `frontend/src/pages/__tests__/ProjectDetail.test.tsx` (extend)

**Interfaces:**
- Consumes: `ProjectOut.my_permissions`, `GET /api/projects/{id}/health` (badges).
- Produces: nested routes under `/projects/:id` —
  `overview` (index), `files`, `jobs`, `tests`, `explore`, `settings`,
  `members`; `ProjectDetail` renders the sidebar plus an `<Outlet />`;
  `/projects/:id` redirects to `overview`.

**Routing is not incidental.** `Tabs` carries no routing today: a reload
returns to the first tab and no tab is linkable. Routing makes reloads
stable and makes "here are this project's test results" a shareable link —
and it is what lets slice ③'s action cards link to
`files?state=new,modified` with the filter already applied.

Grouped as **Knowledge base** (overview, files, jobs), **Retrieval** (tests,
explore), **Manage** (settings, members). Entries are hidden per the
existing `my_permissions` atoms — **no client-side role math**. Sidebar
entries carry live badges: files → count of `new` + `modified`; jobs →
`running`.

- [x] **Step 1: Write the failing routing tests**

```tsx
test("a deep link lands on the right pane", async () => {
  renderApp({ route: "/projects/p1/tests" });
  expect(await screen.findByRole("heading", { name: "檢索測試工作台" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "文件" })).not.toBeInTheDocument();
});

test("/projects/:id redirects to the overview", async () => {
  renderApp({ route: "/projects/p1" });
  expect(await screen.findByText("知識庫健康度")).toBeInTheDocument();
});

test("a reload keeps the pane", async () => {
  const { unmount } = renderApp({ route: "/projects/p1/settings" });
  unmount();
  renderApp({ route: "/projects/p1/settings" });
  expect(await screen.findByRole("heading", { name: "設定" })).toBeInTheDocument();
});

test("entries missing an atom are hidden, not disabled", async () => {
  renderApp({ route: "/projects/p1/overview", myPermissions: ["project:view"] });
  await screen.findByText("知識庫健康度");
  expect(screen.queryByRole("link", { name: "設定" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "成員" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: /文件/ })).toBeInTheDocument();
});

test("sidebar badges come from /health, not from a client-side count", async () => {
  renderApp({ route: "/projects/p1/overview", health: { files: { new: 2, modified: 1 } } });
  expect(await screen.findByText("3")).toBeInTheDocument();
});

test("the files entry links to the state filter the overview uses", async () => {
  renderApp({ route: "/projects/p1/files?state=new,modified" });
  expect(await screen.findByText("draft.md")).toBeInTheDocument();
});
```

- [x] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- ProjectDetail`
Expected: FAIL — `/projects/p1/tests` renders the tab container, not a pane.

- [x] **Step 3: Implement**

`App.tsx` nests the routes; `ProjectDetail` keeps the project and members
queries and the permission computation, drops `Tabs`, and renders
`<ProjectSidebar /> <Outlet context={...} />`. The old overview tab's
`Descriptions` + members card become the `members` pane. `ExplorePanel` and
`SettingsPanel` move under the sidebar unchanged.

- [x] **Step 4-5: Both locales, frontend gate, commit**

```bash
cd frontend && npm test && npm run lint && npx tsc -b --noEmit
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(ui): replace project tabs with a routed second-level sidebar

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 6: Frontend — the health overview and its action card

**Files:**
- Create: `frontend/src/pages/ProjectOverview.tsx`, `frontend/src/components/project/ActionCard.tsx`
- Modify: `frontend/src/App.tsx`, both locales
- Test: `frontend/src/pages/__tests__/ProjectOverview.test.tsx` (new file)

**Interfaces:**
- Consumes: `GET /api/projects/{id}/health` (Task 4).
- Produces: `nextAction(health) -> { key, severity, target }` — a pure function exported from `ActionCard.tsx` and unit-tested without rendering.

**The action card is ONE ordered check**, and each card links to its target
**with filters already applied**:

1. `active_job` → a job is running; link to its log.
2. `has_baseline` **and** (`ingest_check == "unavailable_not_indexed"` **or**
   `artifacts_stale`) → the indexed output is gone, or was left behind by an
   attempt that failed part-way, while the baseline still claims files are
   indexed; run a **full index**. This outranks everything below it because
   every state under it is being read off output that is missing or
   untrustworthy. The card also explains that citation links are switched
   off until this is repaired, so the absence is legible rather than
   mysterious.
3. no `has_baseline` → run a **full index** to establish a trustworthy
   baseline (an update will not do it).
4. `removed > 0` → deleted documents are still answering queries; only a
   **full index** clears them.
5. `new + modified > 0` → rebuild; link to `files?state=new,modified`.
6. `skipped > 0` → documents graphrag did not ingest; link to
   `files?state=skipped`.
7. `latest_run.regressions > 0` → link to the workbench filtered to
   regressions.
8. otherwise healthy.

`removed` outranks `new`/`modified` because it is the only one that needs a
*full* index rather than an update, and an earlier draft omitted it entirely
— a project whose only problem was deleted documents reported itself
healthy. Rule 2 exists for the same class of mistake: **missing output is
not the absence of a problem.**

When `ingest_check == "unavailable_title_column"`, rules 4-6 still apply but
the card adds that silent-skip detection is off, so `skipped` is not
evidence of health either way.

- [x] **Step 1: Write the failing tests**

```ts
const H = (over = {}) => ({
  files: { new: 0, modified: 0, indexed: 5, skipped: 0, removed: 0, total: 5 },
  ingest_check: "available", has_baseline: true, artifacts_stale: false,
  last_index: { job_id: "j0", type: "index", finished_at: "2026-09-04T00:00:00Z" },
  active_job: null, latest_run: null, ...over,
});

test("an active job outranks everything", () => {
  expect(nextAction(H({ active_job: { id: "j1", type: "index" }, files: { ...H().files, removed: 3 } })).key)
    .toBe("activeJob");
});

test("missing output under an existing baseline outranks a missing baseline check", () => {
  expect(nextAction(H({ ingest_check: "unavailable_not_indexed" })).key).toBe("artifactsMissing");
});

test("stale artifacts from a failed attempt reach the same card", () => {
  expect(nextAction(H({ artifacts_stale: true })).key).toBe("artifactsMissing");
});

test("removed outranks new and modified", () => {
  expect(nextAction(H({ files: { ...H().files, removed: 1, new: 4, modified: 2 } })).key)
    .toBe("removed");
});

test("a project whose only problem is deleted documents is NOT healthy", () => {
  expect(nextAction(H({ files: { ...H().files, removed: 1 } })).key).not.toBe("healthy");
});

test("skipped ranks below new and modified", () => {
  expect(nextAction(H({ files: { ...H().files, new: 1, skipped: 3 } })).key).toBe("stale");
  expect(nextAction(H({ files: { ...H().files, skipped: 3 } })).key).toBe("skipped");
});

test("regressions are the last non-healthy card", () => {
  expect(nextAction(H({ latest_run: { regressions: 2 } })).key).toBe("regressions");
});

test("a clean project is healthy", () => {
  expect(nextAction(H()).key).toBe("healthy");
});

test("no baseline asks for a full index and says an update will not do", () => {
  const a = nextAction(H({ has_baseline: false }));
  expect(a.key).toBe("noBaseline");
});
```

```tsx
test("the stale-artifacts card explains that citation links are off", async () => {
  renderOverview({ artifacts_stale: true });
  expect(await screen.findByText(/引用連結會暫時關閉/)).toBeInTheDocument();
});

test("the stale card links to files with the filter applied", async () => {
  renderOverview({ files: { ...clean, new: 2, modified: 1 } });
  const link = await screen.findByRole("link", { name: /查看待索引文件/ });
  expect(link).toHaveAttribute("href", "/projects/p1/files?state=new,modified");
});

test("unavailable title_column adds a caveat without changing the ranking", async () => {
  renderOverview({ ingest_check: "unavailable_title_column", files: { ...clean, removed: 1 } });
  expect(await screen.findByText(/靜默略過偵測已關閉/)).toBeInTheDocument();
  expect(screen.getByText(/只有完整重建/)).toBeInTheDocument();
});
```

- [x] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- ProjectOverview`
Expected: FAIL — module not found.

- [x] **Step 3: Implement**

`nextAction` is a pure `if`-ladder in the order above, returning
`{ key, severity, target }`. `ProjectOverview` renders the card, four stat
tiles (documents, pending, last index, rating summary) and two recent-activity
mini-cards, and consumes `/health` only.

- [x] **Step 4-5: Both locales, frontend gate, commit**

```bash
cd frontend && npm test && npm run lint && npx tsc -b --noEmit
git add frontend/src
git commit -m "$(cat <<'EOF'
feat(ui): add the knowledge-base health overview and its action card

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

### Task 7: Frontend — the citation loop and project-list health

**Files:**
- Modify: `frontend/src/components/tests/AnswerView.tsx`, `frontend/src/components/files/FilePreviewDrawer.tsx`, `frontend/src/pages/Projects.tsx`, both locales
- Test: `frontend/src/components/__tests__/AnswerView.test.tsx` (new), `frontend/src/pages/__tests__/Projects.test.tsx` (extend)

**Interfaces:**
- Consumes: `Citation.entries[].source_name` (Task 2), `POST .../preview` both forms (Task 3), `GET /api/projects/health?ids=` (Task 4).
- Produces: `AnswerView` opens `FilePreviewDrawer` with the locator for the
  citation's origin — `{resultId, entryId}` for a stored run, `{passage}`
  for an ad-hoc query. **Slice ① Task 9's reserved `locator` prop is used
  here**, unchanged.

**Using the `source_name` that arrived with the answer, never a later
lookup.** There is no endpoint to look one up, by design.

**A `source_name` whose file has since been deleted renders as a disabled
link saying the document was removed**, rather than a dead 404 — the
frontend must not call the preview endpoint for it.

- [x] **Step 1: Write the failing tests**

```tsx
test("a Sources citation with a source_name is clickable", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME] });
  await userEvent.click(await screen.findByRole("button", { name: "file-a" }));
  expect(await screen.findByText("PREVIEW-BODY")).toBeInTheDocument();
});

test("a stored run passes the historic locator", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], origin: { resultId: "r1" } });
  await userEvent.click(await screen.findByRole("button", { name: "file-a" }));
  expect(lastPreviewBody).toEqual({ result_id: "r1", entry_id: 7 });
});

test("an ad-hoc answer passes the passage locator", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], origin: null });
  await userEvent.click(await screen.findByRole("button", { name: "file-a" }));
  expect(lastPreviewBody).toEqual({ passage: "the cited passage" });
});

test("a null source_name renders unlinked, with the answer still shown", async () => {
  renderAnswer({ citations: [SOURCES_WITHOUT_NAME] });
  expect(await screen.findByText(/答案內容/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /file-/ })).not.toBeInTheDocument();
});

test("non-Sources labels are never linked", async () => {
  renderAnswer({ citations: [ENTITIES_CITATION] });
  expect(await screen.findByText("Entities")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /file-/ })).not.toBeInTheDocument();
});

test("a deleted source renders disabled and does not call preview", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], removedNames: ["file-a"] });
  const el = await screen.findByText("file-a");
  expect(el.closest("button")).toBeDisabled();
  expect(previewCalls).toBe(0);
  await userEvent.hover(el);
  expect(await screen.findByText(/文件已刪除/)).toBeInTheDocument();
});
```

```tsx
test("project list shows index health from one batch request", async () => {
  renderProjects();
  await screen.findByText("客服知識庫");
  expect(healthRequests).toEqual([
    "/api/projects/health?ids=p1,p2,p3",
  ]);
  expect(screen.getByText("3 待索引")).toBeInTheDocument();
});

test("a project with only removed documents is flagged, not shown healthy", async () => {
  renderProjects({ health: { p1: { files: { removed: 2, new: 0, modified: 0, skipped: 0 } } } });
  expect(await screen.findByText(/已刪除文件仍在索引中/)).toBeInTheDocument();
});
```

- [x] **Step 2: Run to verify they fail**

Run: `cd frontend && npm test -- AnswerView Projects`
Expected: FAIL — citations render as plain text.

- [x] **Step 3: Implement**

`AnswerView` takes an added `origin?: { resultId: string } | null` prop and
renders each `Sources` entry with a `source_name` as a button that opens the
drawer with the matching locator variant. `Projects.tsx` issues one
`GET /api/projects/health?ids=` for the whole visible list.

- [x] **Step 4: Both locales; frontend gate**

Run: `cd frontend && npm test && npm run lint && npx tsc -b --noEmit && npm run build`

- [x] **Step 5: Documentation and release notes**

- `README.md`: the full knowledge-manager loop end to end — upload → index →
  test → find the document at fault → fix → re-index. Mirror into
  `docs/zh-TW/README.md` in the same commit.
- Release notes for this slice: `Sources` citations now carry a
  `source_name` resolved with the answer, and **there is no endpoint that
  resolves one after the fact** — a citation id is only meaningful against
  the artifacts that produced it. State the conservatism plainly: after a
  failed or interrupted index, citation links stay off until a successful
  index promotes a new baseline, and `/health`'s `artifacts_stale` plus
  overview card 2 are where the user sees why.
- Confirm the whole feature added **no environment variables**, so
  `.env.example`, the compose files and the Helm chart are untouched:

```bash
docker compose config
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config
helm lint deploy/helm/graphrag-ui && helm template deploy/helm/graphrag-ui > /dev/null
```

- [x] **Step 6: Full gate and commit**

```bash
cd backend && uv run pytest -q -m "not slow" && uv run ruff check && uv run ruff format --check && uv run mypy
cd ../frontend && npm test && npm run lint && npx tsc -b --noEmit && npm run build
git add frontend/src README.md docs/zh-TW
git commit -m "$(cat <<'EOF'
feat(ui): close the citation-to-document loop and show list health

Claude-Session: https://claude.ai/code/session_01Na6br4wAPnuTpbkiSNgncy
EOF
)"
```

---

## Feature exit criteria

With slice ③ landed, the loop the spec set out to support is closed: a
knowledge manager opens a project and reads its health and the single most
useful next action; uploads, tags and previews documents that each know
their own index state; re-runs a saved question set as a background job
against a named index version; rates the answers and sees the trend; and
clicks a citation to land in the source document at the cited passage — or
is told plainly why the link is not there.

One slow test (`tests/test_real_corpus_titles.py`, slice ① Task 4) pins the
title-recovery rule against real graphrag behavior. If a graphrag bump
breaks it, that test fails and the degradation path is already built:
`ingest_check` reports unavailable, `skipped` stops being emitted, citations
render unlinked, and the four always-computable states remain correct.
