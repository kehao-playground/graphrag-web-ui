# Knowledge-Manager UX: Document Governance & Retrieval Testing — Design

Date: 2026-09-06
Status: approved in chat 2026-09-06; pending implementation plans.

User decisions (2026-09-06, chat):

1. **Scope**: both document management and retrieval testing, plus the
   workflow that connects them. Not a narrow single-panel change.
2. **Backend changes allowed**, including new tables and alembic
   migrations.
3. **Retrieval testing depth**: saved question sets + batch runs bound to
   an index version + **human ratings** (good / fair / poor + note). No
   expected answers, no automated scoring.
4. **File scale**: tens to hundreds per project, grouped by **tags**.
   Filenames stay flat — no `input/` subdirectories.
5. **Project-page IA**: a second-level sidebar inside the project, with a
   **knowledge-base health overview** as the landing page (mockup option
   C).
6. **Test workbench main view**: a **rating matrix** (rows = questions,
   columns = runs), drilling into a single-cell drawer and a two-run
   side-by-side diff (mockup option C).
7. **Batch execution is a background job**, scheduled through the existing
   job runner, not through the interactive query rate limiter.
8. **Delivery**: three vertical slices, each independently shippable.

## 1. Problem

The console is built around the machine's structure (a project has files,
settings, jobs, a query endpoint, artifacts), not around the knowledge
manager's job. That job is a loop:

> upload documents → index → test whether retrieval answers real questions
> → find the document at fault → fix it → re-index

Today the console supports each step in isolation and none of the joins.

**Document management** (`frontend/src/components/FilesPanel.tsx`, 143
lines):

- A flat table with no search, sort, grouping, or bulk selection. At the
  stated scale (hundreds of files) it stops working.
- **No index state per file.** `GET /api/projects/{id}/files` returns
  `{name, size, modified_at}`. Nothing tells the user which documents are
  in the current index, which changed since it was built, or which were
  deleted but still haunt it. The single question a knowledge manager
  asks most — "is my knowledge base current?" — has no answer in the UI.
- No content preview, so a bad answer cannot be traced to a bad document.

**Retrieval testing** (`frontend/src/components/QueryPanel.tsx`, 169
lines):

- Single-shot and stateless. Changing the method or asking a second
  question destroys the previous result. There is no history.
- No concept of a question set, so "did the 20 questions we care about get
  better after this re-index?" requires re-typing 20 questions by hand and
  remembering the old answers.
- Citations render as collapsed text with no link to the source document,
  breaking the loop exactly where it matters: *wrong answer → which
  document → go fix it*.

**Workflow** (`frontend/src/pages/ProjectDetail.tsx`, 302 lines):

- Six sibling tabs (overview / settings / jobs / files / query / explore)
  in an order that matches neither the workflow nor frequency of use, and
  none of them says what to do next.
- `Tabs` carries no routing: a reload returns to the first tab and no tab
  is linkable.
- The project list shows no index health, so staleness is invisible until
  someone opens a project.

## 2. Goals

- Every input file carries a **trustworthy index state**, computed from
  recorded evidence rather than guessed from timestamps.
- Documents are **searchable, taggable, previewable, and bulk-operable**
  at a scale of hundreds.
- A **question set** can be re-run in one action against the current
  index, producing a run bound to the index version that answered it.
- Answers are **human-rated**, and the accumulated ratings show quality
  **trend** per question across runs.
- A citation **resolves to its source document** and opens it.
- The project landing page states **knowledge-base health** and the single
  most useful next action.

## 3. Non-goals

- No automated scoring (LLM-as-judge, citation-hit-rate) and no expected
  answers. Ratings are human, by decision (3).
- No `input/` subdirectories. Tags replace folders, by decision (4).
  `_safe_name` and `input.file_pattern` are untouched.
- No cross-method batch execution (20 questions × 4 methods is 4× the cost
  for unclear value). One run binds one method; comparing methods means
  comparing two runs, which the matrix already does.
- `ExplorePanel` / `GraphView` are unchanged; they move under the new
  sidebar but keep their behavior.
- No new environment variables. The env-name list in AGENTS.md stays
  fixed; bounds that would otherwise be config live as domain constants.

## 4. Information architecture

`ProjectDetail`'s `Tabs` becomes a routed second-level sidebar:

```
/projects/:id/overview    Knowledge-base health + next action  (landing)
/projects/:id/files       Documents
/projects/:id/jobs        Indexing jobs
/projects/:id/tests       Retrieval test workbench
/projects/:id/explore     Graph explorer          (unchanged content)
/projects/:id/settings    settings.yaml + env     (unchanged content)
/projects/:id/members     Members + project info  (from the old overview)
```

Grouped in the sidebar as **Knowledge base** (overview, files, jobs),
**Retrieval** (tests, explore), **Manage** (settings, members). Sidebar
entries carry live badges (e.g. files → count of `new` + `modified`;
jobs → `running`).

Routing is not incidental: it makes reloads stable and makes "here are
this project's test results" a shareable link. `/projects/:id` redirects
to `/overview`. Entries are hidden per the existing `my_permissions`
atoms — no client-side role math (spec §8 of the RBAC design).

## 5. Data model

Five new tables plus one column. All naming follows
`adapters/models.py` conventions (plural snake_case tables, uuid PKs).

### 5.1 Document metadata

```
project_files
  id, project_id → projects.id, name, sha256, size,
  uploaded_by → users.id, uploaded_at
  unique (project_id, name)

file_tags
  id, project_id → projects.id, name
  unique (project_id, name)

file_tag_links
  file_id → project_files.id, tag_id → file_tags.id
  primary key (file_id, tag_id)
```

**`project_files` is metadata, not the source of truth.** `input/` on
disk remains authoritative for existence. `list_files` performs an FS scan
left-joined against `project_files`; a DB row with no file on disk is
reported as `removed` and its metadata pruned on the next successful
index. This avoids the drift class where the DB claims files the
workspace does not have.

`sha256` is computed during the existing streaming write in
`services/files.py::save_file` — the bytes already pass through that loop,
so hashing costs no extra disk read. Files that predate this migration
have `sha256 = NULL` and hash lazily on first listing.

### 5.2 Index snapshots

```
index_snapshots
  id, job_id → jobs.id, project_id → projects.id, created_at
index_snapshot_entries
  snapshot_id → index_snapshots.id, name, sha256
  primary key (snapshot_id, name)
```

Written by the job runner when an `index` or `update` job reaches
`succeeded`: the `{name → sha256}` of `input/` as the indexer saw it.
This is the basis for detecting change, and it is why the state is
trustworthy rather than an mtime heuristic.

### 5.3 Question sets, runs, ratings

```
question_sets
  id, project_id → projects.id, name, created_by → users.id, created_at

questions
  id, set_id → question_sets.id, text, position, created_at

test_runs
  id, project_id → projects.id, set_id → question_sets.id,
  job_id → jobs.id, index_job_id → jobs.id (nullable), method,
  started_at, finished_at

test_results
  id, run_id → test_runs.id, question_id → questions.id,
  answer, citations (json), timings (json), error (nullable)
  unique (run_id, question_id)

result_ratings
  id, result_id → test_results.id unique, score, note,
  rated_by → users.id, rated_at
```

`index_job_id` is the last successful index/update job at run start; it is
what labels a matrix column ("#12 · 09/02") and what makes a run
comparable. It is nullable because a project can be queried before its
first index job row exists in a fresh install.

**Ratings are project-shared, not per-user.** This is a team console; a
maintainer must see the quality judgement their colleague recorded.
`result_ratings` holds one current rating per result (`rated_by` records
who), and re-rating overwrites. The history of who changed what is
carried by the existing audit log (`test.rated`), not by row versioning.

### 5.4 Job parameters

`jobs` gains a nullable `params` JSON column. `argv` stays what it is — a
graphrag CLI argument vector — and is empty for job types that do not
spawn the CLI. A `test_run` job stores `{set_id, method}` in `params`.

## 6. Per-file index state

A pure function in `domain/files.py`, no I/O, table-driven-testable:

```python
def index_state(
    current: FileFacts,             # name, sha256 from disk + project_files
    snapshot: Mapping[str, str],    # name -> sha256 at last successful index
    indexed_titles: Set[str],       # documents.parquet titles
) -> Literal["new", "modified", "indexed", "skipped", "removed"]
```

| State | Rule | What the UI says |
|---|---|---|
| `indexed` | in snapshot, hash matches, title present in `documents.parquet` | — |
| `modified` | in snapshot, hash differs | changed since the last index |
| `new` | not in snapshot | not indexed yet |
| `removed` | in snapshot, absent from `input/` | deleted, but still in the index until you rebuild |
| `skipped` | in snapshot, hash matches, **title absent** from `documents.parquet` | uploaded and indexed, but graphrag did not ingest it — check the format |

`skipped` costs nothing new: both sides of the comparison already exist
(`documents` is a registered table in `domain/artifacts.py` with a
`title` column, read by the existing read-only duckdb adapter). It is the
only state that actively points the user at the job log, because a
silently dropped document is otherwise invisible.

Both `documents.parquet` and the snapshot are needed. The snapshot alone
cannot distinguish `indexed` from `skipped`; `documents.parquet` alone
cannot detect `modified`.

When a project has never been indexed, `snapshot` is empty and every file
is `new` — no special-casing.

## 7. Backend changes

### 7.1 Files service

`services/files.py`:

- `save_file` computes sha256 while streaming and upserts `project_files`
  inside the existing transaction (audit row → flush → `os.replace` →
  commit). The hash is written before the rename, so a failed rename rolls
  back the metadata too.
- `delete_file` removes the `project_files` row in the same transaction as
  the existing audit row and unlink.
- `list_files` returns `index_state`, `tags`, and `sha256` per entry. The
  duckdb read of `documents.parquet` is best-effort: a project with no
  output yet yields an empty title set (`ArtifactsNotIndexedError` is
  caught and treated as "nothing indexed"), never a failed listing.
- New: `add_tags`, `remove_tags`, `list_tags`, `bulk_delete`.
- New: `preview_file(name, max_bytes)` — reads at most a domain constant
  (`PREVIEW_MAX_BYTES = 64 * 1024`) off the event loop via `to_thread`,
  decodes as UTF-8 with `errors="replace"`, and reports truncation.

The `input/` scan (`_scan_input`) already runs in `to_thread`; the DB join
and duckdb read are added around it, not inside it.

### 7.2 A second runner

`services/runner_loop.py::_execute` currently hard-codes
`IndexRunner().run(argv=...)`. It grows a dispatch on `job.type`:

- `index` / `update` → `IndexRunner` (unchanged)
- `test_run` → new `adapters/test_runner.py`

Both return the existing `RunResult`, so claiming, heartbeat, cancellation
polling, terminal-state writing, and `log_path_for` logging are shared
untouched. This is the smallest change that admits a second kind of work;
the alternative (a parallel loop) would duplicate the stale-worker
reconciliation that took real effort to get right.

`domain/jobs.py::JOB_TYPES` gains `test_run`, and `build_argv` rejects it
explicitly — a `test_run` job has no CLI argv and asking for one is a
caller bug, not a silent empty list.

`adapters/test_runner.py` iterates the question set, calling the same
in-process search path the query service uses. **It reaches graphrag only
through `adapters/graphrag_search.py`** — the env-shielded, sole in-process
import site (AGENTS.md). Per question it writes a `test_results` row and
appends a progress line to the job log, so the existing `JobLogViewer`
shows batch progress with no new streaming mechanism. Cancellation is
checked between questions; a cancelled run keeps the results it already
has.

Per-question failures do not fail the run: the `error` column records
them, the matrix shows that cell as errored, and the remaining questions
still execute. A run that gets nothing but errors still finishes as a run
worth looking at.

### 7.3 Rate limiting and cost

Batch execution deliberately does **not** pass through
`services/rate_limit.py`. That limiter guards interactive queries per
`(user, project)` per hour; one 20-question batch would consume an entire
bucket and could be rejected mid-set, leaving a partial run. As a job,
batch execution is instead bounded by `MAX_CONCURRENT_JOBS`, is
cancellable, survives the browser closing, and is auditable.

Cost visibility reuses the jobs preflight confirmation pattern: the launch
dialog states the question count and method before the user commits, the
same way index launches state last-run cost.

A domain constant caps question-set size (`MAX_QUESTIONS_PER_SET = 200`)
so a single run cannot be unbounded. Not an env var, by non-goal.

**Accepted trade-off**: a test run occupies a `MAX_CONCURRENT_JOBS` slot
shared with indexing, so a long batch can delay an index job. This is
honest — both are real work against the same LLM budget and pod — and
visible in the jobs list. Revisit only if it bites.

### 7.4 Citation → source resolution

`text_units.document_id` → `documents.title` gives the input filename.
Both tables are already registered in `domain/artifacts.py` and readable
through the existing read-only duckdb adapter, so this needs a query, not
a new integration. Resolution is best-effort: a citation whose id is absent
from the frame (which `build_citations` already treats as normal) resolves
to nothing and renders unlinked.

### 7.5 Health aggregate

`GET /api/projects/{id}/health` returns file counts by index state, last
successful index job summary, and the latest run's rating distribution —
one call for the overview page rather than the page fanning out to four
endpoints and assembling them client-side.

## 8. API contract

New and changed routes. `openapi.json` is regenerated and diffed in CI,
and `frontend/src/api/types.generated.ts` is regenerated in the same PR
(`npm run gen:types`) — including for docstring-only changes.

| Route | Atom | Notes |
|---|---|---|
| `GET /api/projects/{id}/files` | `project:view` | + `index_state`, `tags`, `sha256` per entry |
| `GET /api/projects/{id}/files/{name}/preview` | `project:view` | truncated UTF-8 text |
| `POST/DELETE /api/projects/{id}/files/{name}/tags` | `project:edit_content` | |
| `GET /api/projects/{id}/tags` | `project:view` | tag catalog + counts |
| `POST /api/projects/{id}/files:bulk-delete` | `project:edit_content` | |
| `GET /api/projects/{id}/health` | `project:view` | overview aggregate |
| `GET/POST/PATCH/DELETE /api/projects/{id}/question-sets[/{sid}]` | view / `project:edit_content` | questions nested |
| `GET /api/projects/{id}/test-runs` | `project:view` | matrix source |
| `POST /api/projects/{id}/test-runs` | `project:run_jobs` | enqueues the `test_run` job |
| `GET /api/test-runs/{rid}/results` | `project:view` | |
| `PUT /api/test-results/{id}/rating` | `project:edit_content` | upsert |
| `GET /api/projects/{id}/citations/{label}/{cid}/source` | `project:view` | → `{name}` or 404 |

Atom choice follows the existing model: reading is `project:view`,
curating content (tags, question sets, ratings) is `project:edit_content`,
and spending compute is `project:run_jobs` — the same atom that gates
indexing, for the same reason.

Existing routes keep their contracts. `GET /api/projects/{id}/jobs`
gains an optional `type` filter so the jobs page can exclude `test_run`
rows without the frontend filtering after the fact.

## 9. Frontend

### 9.1 Slice ① — Document governance

`FilesPanel` splits into `FilesToolbar`, `FilesTable`, and
`FilePreviewDrawer`. The split is not cosmetic: filtering, bulk selection,
and preview added to one 143-line file would produce exactly the kind of
component nobody can safely change.

- **Toolbar**: filename search (client-side; hundreds of rows do not need
  server paging), tag multi-select, index-state filter, and the quota
  progress bar, moved out of its own block between the uploader and the
  table into the toolbar's right edge. Existing `humanBytes` and the
  90%-to-`exception` behavior are preserved.
- **State filter reads a query param.** `?state=new,modified` is the
  landing target for the overview page's action card — slice ① builds the
  entry point, slice ③ links to it.
- **Table**: an index-state column where each state carries a sentence,
  not just a colored dot. `removed` and `skipped` are the two the user has
  never seen before and the two that must explain themselves.
- **Bulk actions**: delete and tag/untag over selected rows. Bulk delete
  confirms with the count and total size — deleting 30 documents is not
  the same act as deleting one, and the dialog should say so.
- **Preview drawer**: props are `{ name, highlight?: string }` from the
  start. Slice ① never passes `highlight`; slice ③ does, when a citation
  opens it.
- **Upload**: `Upload.Dragger` is unchanged, but a persistent "N documents
  not yet indexed" bar appears above the table linking to the jobs page.
  This is where *upload → index* stops being two unrelated acts.

### 9.2 Slice ② — Retrieval test workbench

`QueryPanel` stops being a page and becomes the workbench's **ad-hoc
query** mode. Two execution paths, deliberately different:

- ad-hoc → existing SSE stream (interactive, token-by-token)
- batch → `test_run` job (long, cancellable, survives navigation)

They share one `AnswerView` component (answer + citations + timings).
Sharing it is a requirement, not a convenience: if the same answer
rendered differently in two places, users would reasonably suspect they
had gotten different results. An ad-hoc answer can be saved into a
question set in one action.

- **Matrix**: rows = questions, columns = the most recent runs (default
  5). No virtualization — hundreds of rows × 5 columns is well within
  antd's `Table`, and complexity for imagined scale is complexity now for
  a benefit later. "Regressions only" filters to questions whose newest
  rating is worse than the previous one.
- **Cell → drawer**: answer, citations, rating, note. Two selected cells →
  side-by-side diff.
- **Diff granularity is sentences, not characters.** GraphRAG answers are
  prose; character diffs bury the real change in noise. Sentence splitting
  and comparison are a pure frontend function with its own unit tests.
- **Rating must be fast.** Rating 20 answers with three mouse clicks each
  is how a feature goes unused. With the drawer open, `1`/`2`/`3` rate and
  advance to the next question. This is the least visible decision in the
  slice and the one that determines whether it gets used.

### 9.3 Slice ③ — Wiring

- Routed sidebar replaces `Tabs` (§4).
- **Overview page** consumes `/health`. The action card rule is a single
  ordered check: `modified`/`new` present → rebuild the index; else
  `skipped` present → inspect the documents that were dropped; else
  regressions in the latest run → open the workbench filtered to them;
  else a plain healthy state. Each card links to its target **with filters
  already applied**, which is what makes it an action rather than a label.
- **Citation loop closes**: `AnswerView` citations become clickable,
  resolve to a filename, and open `FilePreviewDrawer` with `highlight` set
  to the cited text unit. Slice ①'s reserved prop is used here.
- **Project list** gains an index-health column so staleness is visible
  before entering a project.

### 9.4 i18n

Every new string lands in both `zh-TW` and `en-US` in the same PR;
`i18n.test.ts` enforces parity. The five index states and the three rating
levels are user-facing vocabulary — they get real sentences in both
locales, not terse labels transliterated from the schema.

## 10. Testing

Backend:

- `domain/files.py::index_state` — table-driven over all five states plus
  boundaries (never indexed, empty snapshot, NULL sha256 from the
  pre-migration backfill path). This function is the foundation of slice
  ①; if it is wrong, everything built on it is wrong and looks right.
- `services/files.py` — sha256 written on upload; metadata rolled back
  when the rename fails; listing when `documents.parquet` is absent;
  tag add/remove; bulk delete atomicity; preview truncation and
  non-UTF-8 bytes.
- Snapshot written only on `succeeded`, not on `failed`/`cancelled`.
- `runner_loop` dispatch — `test_run` reaches the new runner and never
  `IndexRunner`; `build_argv("test_run", ...)` raises.
- `test_runner` — per-question failure isolation, cancellation between
  questions, progress lines in the job log.
- Route-level authz for every new endpoint against the atom table in §8.

Frontend:

- `FilesPanel` tests extended, not replaced, for the split components.
- Sentence-diff pure function.
- Matrix filtering ("regressions only") and keyboard rating.
- Routed sidebar: deep links land on the right pane; hidden entries for
  missing atoms.

The existing suites stay green: 365 backend, 101 frontend at the time of
writing.

## 11. Compatibility & risks

- **Migration is additive.** No existing column changes type or meaning;
  `jobs.params` is nullable. Projects indexed before this ships have no
  snapshot, so every file reads `new` until the next index — accurate, if
  briefly noisy, and self-correcting on the first rebuild. Call this out
  in the release notes rather than backfilling a snapshot we cannot honestly
  reconstruct.
- **`documents.title` is assumed to equal the input filename.** Verified
  against graphrag 3.1.0's registered `documents` schema in
  `domain/artifacts.py`, but it is an upstream convention, not a contract.
  The implementation asserts it against a real indexed workspace in a slow
  test; if it breaks, `skipped` degrades to "unknown" and the other four
  states are unaffected.
- **Test runs share the job concurrency budget** with indexing (§7.3).
- **In-process search load**: the runner executes inside the API process,
  as interactive queries already do. A batch is many sequential queries,
  not a new class of load, but it is sustained. `MAX_CONCURRENT_JOBS`
  is the throttle.
- **FS/DB drift** for `project_files` is bounded by making the filesystem
  authoritative for existence (§5.1).

## 12. Documentation

- `README.md` gains the knowledge-manager workflow; `docs/zh-TW/` mirror
  updated in the same PR.
- `openapi.json` and `types.generated.ts` regenerated together.
- No new environment variables, so `.env.example`, compose files, and the
  Helm chart are untouched.

## 13. Delivery

Three vertical slices, each shippable on its own:

1. **Document governance** — §5.1, §5.2, §6, §7.1, §9.1. Ends with files
   that know their own index state, tags, search, bulk operations, and
   preview.
2. **Retrieval testing** — §5.3, §5.4, §7.2, §7.3, §9.2. Ends with saved
   question sets, batch runs bound to an index version, the rating matrix,
   and the run diff.
3. **Wiring** — §4, §7.4, §7.5, §9.3. Ends with the routed sidebar, the
   health overview with its action card, the citation-to-document loop,
   and index health on the project list.

Stopping after any slice leaves a coherent product, not a half-built one.
Each gets its own implementation plan under `docs/superpowers/plans/`.
