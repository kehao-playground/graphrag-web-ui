# Knowledge-Manager UX: Document Governance & Retrieval Testing — Design

Date: 2026-09-06
Status: revised after review 2026-09-06 (four P1 blockers); pending
implementation plans.

User decisions (2026-09-06, chat):

1. **Scope**: both document management and retrieval testing, plus the
   workflow that connects them.
2. **Backend changes allowed**, including new tables and alembic
   migrations.
3. **Retrieval testing depth**: saved question sets + batch runs bound to
   an index version + **human ratings** (good / fair / poor + note). No
   expected answers, no automated scoring.
4. **File scale**: tens to hundreds per project, grouped by **tags**.
   Filenames stay flat — no `input/` subdirectories.
5. **Project-page IA**: a second-level sidebar inside the project, with a
   **knowledge-base health overview** as the landing page.
6. **Test workbench main view**: a **rating matrix** (rows = questions,
   columns = runs), drilling into a single-cell drawer and a two-run
   side-by-side diff.
7. **Batch execution is a background job**, scheduled through the existing
   job runner, not through the interactive query rate limiter.
8. **Delivery**: three vertical slices, each independently shippable.
9. **Review round (2026-09-06)**: `documents.title` is recovered to a
   filename **by rule**, with a response-level availability flag when the
   rule cannot hold (§6.2). CSV/JSON projects keep full capability.

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
  recorded evidence rather than guessed from timestamps, and honest about
  its own limits when the evidence is incomplete.
- Documents are **searchable, taggable, previewable, and bulk-operable**
  at a scale of hundreds.
- A **question set** can be re-run in one action against the current
  index, producing a run bound to the index version that answered it.
- Answers are **human-rated**, and the accumulated ratings show quality
  **trend** per question across runs, without historical rows changing
  meaning when the question set is edited.
- A citation **resolves to its source document** and opens it at the cited
  passage.
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
- **Only `Sources` citations are linkable** (§7.4). `Entities`, `Reports`,
  `Relationships` and `Communities` aggregate many text units across many
  documents; a single-document link would be a lie.
- `ExplorePanel` / `GraphView` are unchanged; they move under the new
  sidebar but keep their behavior.
- No new environment variables. Bounds that would otherwise be config live
  as domain constants.

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
atoms — no client-side role math.

## 5. Data model

**Ten new tables plus two columns on `jobs`.** Naming follows
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
reported as `removed` and its metadata pruned when a full index next makes
the removal real.

`sha256` is computed during the existing streaming write in
`services/files.py::save_file` — the bytes already pass through that loop,
so hashing costs no extra disk read. Files that predate this migration
have `sha256 = NULL` and hash lazily on first listing.

### 5.2 Index snapshots

```
index_snapshots
  id, job_id → jobs.id unique, project_id → projects.id,
  kind ('start' | 'baseline'), created_at
index_snapshot_entries
  snapshot_id → index_snapshots.id, name, sha256
  primary key (snapshot_id, name)
```

The first design captured the snapshot **after** a job succeeded and made
it the whole of `input/`. Review showed two independent ways that lies.

**Lie 1: the input can change under the indexer.** Uploads and deletes are
not gated by the job mutex, so `os.replace` can land mid-run. If the CLI
read the old bytes and the file is overwritten before the job finishes, a
post-hoc scan records the *new* hash and the UI reports `indexed` for
content that was never indexed.

**Lie 2: `update` does not do what a full scan implies.** GraphRAG 3.1.0's
`get_delta_docs`
(`graphrag/index/update/incremental_index.py:46`) compares **titles only**
and `load_update_documents.py:62` returns `delta_documents.new_inputs`
alone — `deleted_inputs` is computed and discarded, and `concat_dataframes`
is `old + delta`. So after an `update`:

- a same-name file whose **content changed** was *not* re-ingested;
- a **deleted** file is still fully present in the index.

Replacing the baseline with the current `input/` would erase exactly the
`modified` and `removed` states that were correct.

The design therefore has three parts.

**(a) Snapshot at job start.** The runner writes the `start` snapshot from
`input/` before the CLI is spawned.

**(b) Input freeze during a job.** `save_file`, `delete_file`, and bulk
delete raise `ProjectIndexingError` (→ 409, code `project_indexing`) while
the project has a `queued` or `running` job. The existing
`jobs_one_active_per_project` partial unique index guarantees at most one
such job, so the freeze is a single lookup, not a lock protocol. Together
with (a), the snapshot is what the indexer actually saw.

**(c) Type-specific baseline advancement**, on success only:

| Job type | New baseline |
|---|---|
| `index` | the `start` snapshot, wholesale — a full rebuild really does replace the index |
| `update` | previous baseline, plus entries from the `start` snapshot **for names absent from the previous baseline**. Names already present keep their **old** hash; names absent from `input/` stay in the baseline |

Rule (c) for `update` is not a conservative hedge — it is a precise mirror
of upstream behavior. A modified file correctly stays `modified` (its
old content is what is indexed). A deleted file correctly stays `removed`
(it really is still in the index). Both resolve only after a full `index`.

**Baseline bootstrap.** Projects indexed before this ships have no
baseline, so every file reads `new` until the next index. A trustworthy
baseline requires **a full `index`, never an `update`** — an `update`
against an empty baseline would record only the names it happened to
ingest and inherit none of the history it cannot know. This is stated in
the release notes, and the overview action card says it in the product
when a project has no baseline.

### 5.3 Question sets, runs, ratings

```
question_sets
  id, project_id → projects.id, name, created_by → users.id, created_at

questions
  id, set_id → question_sets.id, lineage_id, text, position,
  created_by → users.id, created_at, archived_at (nullable)

test_runs
  id, project_id → projects.id, set_id → question_sets.id,
  job_id → jobs.id, index_job_id → jobs.id (nullable), method,
  started_at, finished_at

test_results
  id, run_id → test_runs.id, question_id → questions.id,
  question_text, answer, citations (json), timings (json),
  error (nullable)
  unique (run_id, question_id)

result_ratings
  id, result_id → test_results.id unique, score, note,
  rated_by → users.id, rated_at
```

**Historic runs must not change meaning when the question set is edited.**
Storing only `question_id` let an edited question retro-label an old
answer, and a deleted question forced a choice between blocking the delete
and destroying history. Two mechanisms, both cheap:

- `test_results.question_text` is the question **as asked**, denormalized
  at run time. A historic run is self-contained.
- Questions are **immutable once referenced by a run**. Editing such a
  question inserts a new row sharing the original `lineage_id` and sets
  `archived_at` on the old one; deleting sets `archived_at`. A question
  never referenced by a run is still edited in place — fixing a typo
  before the first run must not fork history.

The matrix's rows are **lineages**, not question rows, so a question keeps
one row across edits while each cell shows the text actually asked.

`index_job_id` is the last successful index/update job at run start; it
labels a matrix column ("#12 · 09/02") and is what makes runs comparable.
It is nullable for a project queried before any index job row exists.

**Ratings are project-shared, not per-user.** This is a team console; a
maintainer must see the quality judgement their colleague recorded.
`result_ratings` holds one current rating per result (`rated_by` records
who), and re-rating overwrites. Who changed what is carried by the
existing audit log (`test.rated`), not by row versioning.

### 5.4 Job columns

`jobs` gains:

- `params` (JSON, nullable) — a `test_run` job stores `{set_id, method}`.
  `argv` stays what it is, a graphrag CLI argument vector, and is empty
  for job types that spawn no CLI.
- `progress` (JSON, nullable) — `{done, total}`, written by the test-run
  service between questions so the UI can show batch progress without a
  new streaming mechanism.

## 6. Per-file index state

### 6.1 The four states that always hold

A pure function in `domain/files.py`, no I/O:

```python
def index_state(
    name: str,
    current_sha: str | None,          # None when the file is gone from input/
    baseline: Mapping[str, str],      # name -> sha256, the current baseline
    ingested: IngestCheck,            # available(titles) | unavailable(reason)
) -> FileIndexState
```

| State | Rule |
|---|---|
| `new` | not in baseline |
| `modified` | in baseline, hash differs |
| `removed` | in baseline, absent from `input/` |
| `indexed` | in baseline, hash matches |

These four need only the baseline. They are always computable.

### 6.2 `skipped` is a refinement, and it is optional

`skipped` — in the baseline with a matching hash, yet **absent from
`documents.parquet`** — means graphrag was handed the file and did not
ingest it. It is the only state that points the user at the job log,
because a silently dropped document is otherwise invisible.

It requires mapping a `documents.title` back to a filename, and review
established that this mapping is **not** always available:

- `graphrag_input/text.py` sets `title = Path(path).name` — the filename.
- `graphrag_input/structured_file_reader.py:48-53` sets
  `title = f"{path}{num}"` where `num` is `" (N)"` for multi-row files,
  and uses the **row's column value** when `input.title_column` is
  configured.

So for CSV/JSON the title is `report.csv (0)`, or arbitrary row data.

**Recovery rule** (pure, in `domain/artifacts.py`):

1. If `input.title_column` is set in `settings.yaml` → the refinement is
   **unavailable**; no title can be attributed to a file.
2. A title matching an existing `input/` filename exactly wins. This is
   checked first, so a single-row `report (1).csv` is not mangled by
   rule 3.
3. Otherwise strip a trailing ` (N)` and accept the result only if it
   matches an existing filename.
4. Otherwise the title maps to nothing.

We never write `title_column` ourselves — `adapters/workspace.py:103-105`
sets only `input.type` and `input.file_pattern` — but `SettingsPanel`
lets a user hand-edit `settings.yaml`, so rule 1 is reachable and must be
detected rather than assumed away.

**Availability is reported, not guessed.** `GET .../files` returns

```
ingest_check: "available"
            | "unavailable_not_indexed"   // no documents.parquet yet
            | "unavailable_title_column"  // settings.yaml sets title_column
```

When it is not `available`, **`skipped` is never emitted** and the UI says
that silent-skip detection is off, and why. The first design's "no parquet
→ empty title set" would have marked every baseline file `skipped` on a
project whose output was missing — turning a diagnostic into a screen of
false alarms.

`skipped` therefore is a sixth possible value of the state field, emitted
only when the check is available. The availability flag lives on the
response, not on every file row: it is a property of the project's
configuration, and repeating it per file would invite the UI to render it
per file.

## 7. Backend changes

### 7.1 Files service

`services/files.py`:

- `save_file` computes sha256 while streaming and upserts `project_files`
  inside the existing transaction (audit row → flush → `os.replace` →
  commit), so a failed rename rolls back the metadata too.
- `save_file`, `delete_file` and bulk delete first check the **input
  freeze** (§5.2b).
- `delete_file` removes the `project_files` row in the same transaction as
  the audit row and unlink.
- `list_files` returns `index_state`, `tags`, and `sha256` per entry plus
  the response-level `ingest_check`. A missing `documents.parquet`
  (`ArtifactsNotIndexedError`) yields `unavailable_not_indexed`, never an
  empty title set.
- New: `add_tags`, `remove_tags`, `list_tags`, `bulk_delete`.
- New: `preview_file(name, *, find=None)` — reads at most
  `PREVIEW_MAX_BYTES = 64 * 1024` off the event loop via `to_thread`,
  decodes UTF-8 with `errors="replace"`, and reports truncation. With
  `find`, it returns the window **around the first match** and
  `match: true`; with no match it returns the head window and
  `match: false` rather than pretending.

### 7.2 Layering: a service owns the batch, an adapter owns I/O

The first design put question iteration, result persistence and
cancellation in `adapters/test_runner.py`. That is use-case and
transaction orchestration, which AGENTS.md places in `services/`.

- `services/test_runs.py` — iterates the question set, writes
  `test_results` and `jobs.progress`, checks cancellation between
  questions, owns the transaction boundary.
- graphrag is reached only through the existing env-shielded
  `adapters/graphrag_search.py`; no new graphrag import site.

**A shared query core is required, not optional.** `services/query.py`
`run_query` applies the rate limiter on its first line (`query.py:110`),
before any I/O. A batch that must bypass the limiter yet produce identical
frames, citations and timings cannot call it. So:

```python
async def _execute_query(project, method, query, response_type) -> dict
    # config load → frames → search → citations → timings. No limiter.

async def run_query(project, user, method, query, response_type) -> dict
    get_rate_limiter().check(str(user.id), str(project.id))
    return await _execute_query(...)
```

Interactive queries keep their exact behavior; the batch service calls
`_execute_query`. One code path produces every answer in the product.

`services/runner_loop.py::_execute` currently hard-codes
`IndexRunner().run(argv=...)`. It grows a dispatch on `job.type`:
`index`/`update` → `IndexRunner` (unchanged), `test_run` →
`services/test_runs.py`. Both yield the existing `RunResult`, so claiming,
heartbeat, cancellation polling, terminal-state writing and
`log_path_for` logging are shared untouched — the smallest change that
admits a second kind of work without duplicating the stale-worker
reconciliation.

`domain/jobs.py::JOB_TYPES` gains `test_run`, and `build_argv` rejects it
explicitly: a `test_run` job has no CLI argv, and asking for one is a
caller bug, not a silent empty list.

Per-question failures do not fail the run: `test_results.error` records
them, the matrix marks that cell errored, and the remaining questions
still execute. Cancellation keeps the results already produced.

### 7.3 Concurrency, rate limiting, cost

`jobs_one_active_per_project`
(`migrations/versions/47b77c99bc8f_indexing_jobs.py:53-59`) is a partial
unique index on `project_id` where `status IN ('queued','running')` — with
**no type predicate**. Adding `test_run` therefore makes test runs and
index jobs **mutually exclusive per project, in both directions**.

Kept deliberately, for three reasons: a test run whose index changes
underneath it is meaningless, so `test_runs.index_job_id` stays honest;
`FrameCache`'s `(path, mtime, size)` validity key would otherwise reload
parquet files mid-write; and it is what makes the input freeze (§5.2b) a
single check. The costs are real and must appear in the product:
`POST /test-runs` returns **409 `job_conflict`** while an index runs, and
the workbench shows "indexing in progress — the run will be available
when it finishes" rather than a dead button. The same applies in reverse
on the jobs page.

Batch execution deliberately does **not** pass through
`services/rate_limit.py`. That limiter guards interactive queries per
`(user, project)` per hour; one 20-question batch would consume an entire
bucket and could be rejected mid-set, leaving a partial run. As a job it
is instead bounded by `MAX_CONCURRENT_JOBS`, cancellable, survives the
browser closing, and is auditable.

Cost visibility reuses the jobs preflight confirmation: the launch dialog
states the question count and method before the user commits, as index
launches state last-run cost. `MAX_QUESTIONS_PER_SET = 200` (domain
constant, not an env var) bounds a single run.

### 7.4 Citation → source resolution

Only **`Sources`** citations resolve. `domain/artifacts.py` registers
`text_units` with a singular `document_id`, so one text unit maps to one
document, and `documents.title` maps back to a filename through §6.2's
rule. `Entities`, `Reports`, `Relationships` and `Communities` each
summarize many text units spanning many documents; the UI renders them
unlinked rather than picking one arbitrarily.

Resolution is best-effort throughout: a citation id absent from the frame
(which `build_citations` already treats as normal), a title that maps to
nothing, or `unavailable_title_column` all render unlinked. The endpoint
returns the filename **and the cited text-unit text**, which the UI passes
to `preview?find=` so the drawer opens at the passage instead of at byte
zero — a 64 KiB head window frequently would not contain it.

### 7.5 Health aggregates

```
GET /api/projects/{id}/health
  files: {new, modified, indexed, skipped, removed, total}
  ingest_check: <as §6.2>
  has_baseline: bool
  last_index: {job_id, type, finished_at} | null
  active_job: {id, type} | null
  latest_run: {run_id, set_id, method, index_job_id,
               ratings: {good, fair, poor, unrated},
               regressions: int} | null
```

`regressions` is computed server-side (count of lineages whose newest
rating is worse than the previous run's) because the overview must state
it without downloading every result — the first design promised only a
rating distribution, which cannot answer the question the action card
asks.

```
GET /api/projects/health?ids=<uuid,uuid,...>
```

returns the compact subset the project list needs (`files.new`,
`files.modified`, `files.removed`, `has_baseline`, `last_index.finished_at`),
one round trip for the whole list. Without it the list would issue one
request per project.

## 8. API contract

`openapi.json` is regenerated and diffed in CI;
`frontend/src/api/types.generated.ts` is regenerated in the same PR
(`npm run gen:types`), including for docstring-only changes.

| Route | Atom | Notes |
|---|---|---|
| `GET /api/projects/{id}/files` | `project:view` | + `index_state`, `tags`, `sha256`; response-level `ingest_check` |
| `POST /api/projects/{id}/files` | `project:edit_content` | **409 `project_indexing`** while a job is active |
| `DELETE /api/projects/{id}/files/{name}` | `project:edit_content` | same 409 |
| `POST /api/projects/{id}/files:bulk-delete` | `project:edit_content` | same 409 |
| `GET /api/projects/{id}/files/{name}/preview` | `project:view` | `?find=` → window around the match, `match` flag |
| `POST/DELETE /api/projects/{id}/files/{name}/tags` | `project:edit_content` | not frozen — tags are metadata, not input |
| `GET /api/projects/{id}/tags` | `project:view` | catalog + counts |
| `GET /api/projects/{id}/health` | `project:view` | §7.5 |
| `GET /api/projects/health?ids=` | `project:view` | filtered to visible projects |
| `GET/POST/PATCH/DELETE /api/projects/{id}/question-sets[/{sid}]` | view / `project:edit_content` | questions nested; PATCH follows §5.3 immutability |
| `GET /api/projects/{id}/test-runs` | `project:view` | matrix source; rows keyed by lineage |
| `POST /api/projects/{id}/test-runs` | `project:run_jobs` | enqueues the job; **409 `job_conflict`** while indexing |
| `GET /api/test-runs/{rid}/results` | `project:view` | includes `question_text` |
| `PUT /api/test-results/{id}/rating` | `project:edit_content` | upsert |
| `GET /api/projects/{id}/citations/sources/{cid}/source` | `project:view` | → `{name, text}` or 404; Sources only |

Atom choice follows the existing model: reading is `project:view`,
curating content (tags, question sets, ratings) is `project:edit_content`,
spending compute is `project:run_jobs` — the same atom that gates
indexing, for the same reason.

`GET /api/projects/{id}/jobs` gains an optional `type` filter so the jobs
page can exclude `test_run` rows server-side.

## 9. Frontend

### 9.1 Slice ① — Document governance

`FilesPanel` splits into `FilesToolbar`, `FilesTable`, and
`FilePreviewDrawer`. Filtering, bulk selection and preview added to one
143-line file would produce a component nobody can safely change.

- **Toolbar**: filename search (client-side; hundreds of rows do not need
  server paging), tag multi-select, index-state filter, and the quota
  progress bar, moved out of its own block between the uploader and the
  table into the toolbar's right edge. Existing `humanBytes` and the
  90%-to-`exception` behavior are preserved.
- **State filter reads a query param.** `?state=new,modified` is the
  landing target for the overview action card — slice ① builds the entry
  point, slice ③ links to it.
- **Table**: an index-state column where each state carries a sentence,
  not just a colored dot. `removed` and `skipped` are the two the user has
  never seen before, and both must explain themselves — `removed`
  specifically must say that only a **full rebuild** clears it.
- When `ingest_check` is not `available`, the table shows one banner
  explaining that silent-skip detection is off and which reason applies.
- **Frozen state**: while a job is active, the uploader and delete actions
  are disabled with the reason shown, rather than letting the user
  discover the 409.
- **Bulk actions**: delete and tag/untag over selected rows. Bulk delete
  confirms with count and total size — deleting 30 documents is not the
  same act as deleting one.
- **Preview drawer**: props `{ name, find?: string }`. Slice ① never
  passes `find`; slice ③ does, when a citation opens it.
- **Upload**: `Upload.Dragger` unchanged, but a persistent "N documents
  not yet indexed" bar appears above the table linking to the jobs page.

### 9.2 Slice ② — Retrieval test workbench

`QueryPanel` stops being a page and becomes the workbench's **ad-hoc
query** mode. Two execution paths, deliberately different:

- ad-hoc → existing SSE stream (interactive, token-by-token)
- batch → `test_run` job (long, cancellable, survives navigation)

Both render through one `AnswerView` (answer + citations + timings), and
after §7.2 both are produced by one `_execute_query`. If the same answer
rendered differently in two places, users would reasonably suspect they
had gotten different results. An ad-hoc answer saves into a question set
in one action.

- **Matrix**: rows = question **lineages**, columns = the most recent runs
  (default 5). No virtualization — hundreds of rows × 5 columns is well
  within antd's `Table`; complexity for imagined scale is complexity now
  for a benefit later. "Regressions only" uses the same definition the
  backend reports in `/health`.
- **Cell → drawer**: the question text as asked, answer, citations,
  rating, note. Two selected cells → side-by-side diff.
- **Diff granularity is sentences, not characters.** GraphRAG answers are
  prose; character diffs bury the real change in noise. Sentence splitting
  and comparison are a pure frontend function with unit tests.
- **Rating must be fast.** With the drawer open, `1`/`2`/`3` rate and
  advance. Rating 20 answers at three mouse clicks each is how a feature
  goes unused; this is the least visible decision in the slice and the one
  that determines whether it gets used.
- **Editing a question that has runs** warns that it starts a new version
  and that past runs keep the old wording (§5.3).

### 9.3 Slice ③ — Wiring

- Routed sidebar replaces `Tabs` (§4).
- **Overview page** consumes `/health`. The action card is one ordered
  check, and each card links to its target **with filters already
  applied**:

  1. `active_job` → indexing in progress; link to the job log.
  2. no `has_baseline` → run a **full index** to establish a trustworthy
     baseline (an update will not do it — §5.2).
  3. `removed > 0` → deleted documents are still answering queries; only
     a **full index** clears them.
  4. `new + modified > 0` → rebuild; link to `files?state=new,modified`.
  5. `skipped > 0` → documents graphrag did not ingest; link to
     `files?state=skipped`.
  6. `latest_run.regressions > 0` → link to the workbench filtered to
     regressions.
  7. otherwise healthy.

  `removed` outranks `new`/`modified` because it is the only one that
  needs a *full* index rather than an update, and the first design omitted
  it entirely — a project whose only problem was deleted documents
  reported itself healthy.
- **Citation loop closes**: `AnswerView`'s `Sources` citations become
  clickable, resolve to `{name, text}`, and open `FilePreviewDrawer` with
  `find` set to the cited text so the drawer lands on the passage. Slice
  ①'s reserved prop is used here.
- **Project list** gains an index-health column fed by
  `GET /api/projects/health?ids=`.

### 9.4 i18n

Every new string lands in both `zh-TW` and `en-US` in the same PR;
`i18n.test.ts` enforces parity. The six index states, three
`ingest_check` reasons, three rating levels, and the two 409 conditions
are user-facing vocabulary — they get real sentences in both locales, not
labels transliterated from the schema.

## 10. Testing

Backend:

- `domain/files.py::index_state` — table-driven over all states plus
  boundaries: never indexed, empty baseline, `NULL` sha256 from the
  backfill path, and every `ingest_check` value (asserting `skipped` is
  unreachable when unavailable).
- **Baseline advancement** — `index` replaces wholesale; `update` keeps
  old hashes for known names and adds only unknown ones; a modified file
  stays `modified` and a deleted file stays `removed` across an `update`;
  a snapshot is written only on `succeeded`.
- **Input freeze** — upload/delete/bulk-delete return 409 while a job is
  queued or running, and succeed once it is terminal.
- **Title recovery** — exact-match precedence over ` (N)` stripping
  (a single-row `report (1).csv` must not become `report`), and
  `title_column` forcing `unavailable_title_column`.
- A **slow test** against a real indexed workspace covering `text`,
  multi-row CSV, multi-row JSON, and a `title_column` project — the rule
  in §6.2 is read off upstream source and must be pinned against upstream
  behavior, not trusted.
- `runner_loop` dispatch — `test_run` reaches the service and never
  `IndexRunner`; `build_argv("test_run", ...)` raises.
- `_execute_query` / `run_query` — the core applies no limiter; the
  wrapper does; both produce identical bodies.
- `services/test_runs.py` — per-question failure isolation, cancellation
  between questions, `jobs.progress` updates.
- Question immutability — editing an unused question mutates in place;
  editing a referenced one forks the lineage and archives the old row;
  historic `test_results` keep their `question_text`.
- `/health` `regressions` arithmetic, and `?ids=` filtered to visible
  projects.
- Route-level authz for every new endpoint against §8.

Frontend:

- `FilesPanel` tests extended, not replaced, for the split components;
  frozen-state affordances; `ingest_check` banner.
- Sentence-diff pure function.
- Matrix lineage rows, "regressions only", keyboard rating.
- Routed sidebar: deep links land on the right pane; hidden entries for
  missing atoms.

Existing suites stay green: 365 backend, 101 frontend at time of writing.

## 11. Compatibility & risks

- **Migration is additive.** No existing column changes type or meaning;
  `jobs.params` and `jobs.progress` are nullable. Existing projects have
  no baseline, so every file reads `new` until a **full index** — accurate,
  briefly noisy, self-correcting, and surfaced as action card (2) rather
  than left for the user to infer.
- **The input freeze is a behavior change** for an existing endpoint:
  uploads during indexing used to succeed and now 409. That is the point —
  they were silently corrupting the index state — but it is a visible
  change and belongs in the release notes.
- **Test runs and index jobs are mutually exclusive per project** (§7.3),
  including when the global `MAX_CONCURRENT_JOBS` budget is free.
- **The title recovery rule tracks upstream implementation, not a
  contract.** It is read from `graphrag_input/` in 3.1.0 and pinned by the
  slow test above. If a graphrag bump breaks it, the slow test fails, and
  the degradation path (`ingest_check` unavailable, four states still
  correct) is already built.
- **In-process search load**: the batch runs inside the API process, as
  interactive queries already do. It is many sequential queries, not a new
  class of load, but sustained; `MAX_CONCURRENT_JOBS` is the throttle.
- **FS/DB drift** for `project_files` is bounded by keeping the filesystem
  authoritative for existence (§5.1).

## 12. Documentation

- `README.md` gains the knowledge-manager workflow; `docs/zh-TW/` mirror
  updated in the same PR.
- Release notes cover the input freeze and the full-index baseline
  requirement.
- `openapi.json` and `types.generated.ts` regenerated together.
- No new environment variables, so `.env.example`, compose files, and the
  Helm chart are untouched.

## 13. Delivery

Three vertical slices, each shippable on its own:

1. **Document governance** — §5.1, §5.2, §6, §7.1, §9.1. Ends with files
   that know their own index state (and say when they cannot), the input
   freeze, tags, search, bulk operations, and preview.
2. **Retrieval testing** — §5.3, §5.4, §7.2, §7.3, §9.2. Ends with the
   shared query core, saved question sets with stable identity, batch runs
   bound to an index version, the rating matrix, and the run diff.
3. **Wiring** — §4, §7.4, §7.5, §9.3. Ends with the routed sidebar, the
   health overview and its action card, the citation-to-passage loop, and
   index health on the project list.

Stopping after any slice leaves a coherent product. Each gets its own
implementation plan under `docs/superpowers/plans/`.
