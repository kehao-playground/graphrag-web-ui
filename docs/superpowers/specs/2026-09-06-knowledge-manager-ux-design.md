# Knowledge-Manager UX: Document Governance & Retrieval Testing — Design

Date: 2026-09-06
Status: revised after two review rounds 2026-09-06; pending
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
9. **Review round 1**: `documents.title` is recovered to a filename **by
   rule**, with a response-level availability flag when the rule cannot
   hold (§6.3). CSV/JSON projects keep full capability.
10. **Review round 2**: the input freeze is serialized by a project-row
    lock, not a bare check (§5.2b), and is scoped to `index`/`update`
    jobs only — a `test_run` reads `output/` and does not block document
    work. Baseline advancement is driven by attributable document titles,
    not filenames (§5.2c). A run's question manifest is materialized at
    enqueue (§5.3).
11. **Review round 3**: baseline advancement for a known name is
    conditioned on `N ∉ pre` alone (§5.2c). Title recovery is evaluated
    when artifacts are produced and stored on the baseline snapshot, so
    listing never reads today's `settings.yaml`, and `settings.yaml`
    writes join the job freeze (§6.3). File listings enumerate
    `input/` ∪ baseline so `removed` has a source (§6.1). Question
    `PATCH`/`DELETE` take the project lock (§5.3).
12. **Review round 4**: `.env` mutations join the config freeze, since
    graphrag substitutes `${...}` into `settings.yaml` from it (§6.3). A
    successful `update` always advances the baseline row and pointer even
    when entries carry forward unchanged (§5.2c). Citation identity for a
    stored run is materialized at run time, because `human_readable_id` is
    reassigned per build (§7.4).
13. **Review round 5**: the `.env` freeze applies to the **existing**
    `PATCH /{pid}/env` and `DELETE /{pid}/env/{key}` routes — no route
    redesign. The historic preview locator verifies three bindings
    (result→project, entry→result and Sources, `source_name`→path name),
    404 on any mismatch (§7.4).
14. **Review round 6**: `_prepare_query` + `_execute_query` share the
    preamble and tail; SSE keeps `stream_query` — the streaming and
    non-streaming searches are not one function (§7.2). `source_name` is
    resolved with every answer, so no endpoint resolves a citation id
    after the fact, and the preview locator moves into a `POST` body
    (§7.4). The adapter resolver returns titles; filenames come from the
    baseline's provenance (§7.4). A run loads configuration once (§7.2).
15. **Review round 7**: citation enrichment is bracketed by a generation
    guard, since the `documents` read cannot be prevented from crossing a
    rebuild on the ad-hoc path (§7.4). Title matching uses the baseline's
    **entry names**, not `attributable_titles`, so documents deleted from
    `input/` but still indexed stay linkable (§6.3, §7.4).
    `test_runs.config_revision` digests `settings.yaml` **and** `.env`,
    captured atomically with the config load (§7.2).
16. **Review round 8**: the generation guard is stated normatively in
    §7.4 as a G0/G1 sequence with G1 **after** the `documents` read, and
    citation resolution matches titles against the baseline's **entry
    names** — `attributable_titles` is only ever used to decide `skipped`.
    `config_revision` becomes `workspace_config_revision` with a framed
    digest and a claim narrowed to the workspace files (§7.2).
17. **Review round 9**: the generation guard also compares
    `projects.artifact_epoch`, incremented in every `index`/`update`
    enqueue transaction. The baseline pointer alone is an ABA hazard — a
    failed job rewrites `output/` in place and promotes nothing, so both
    guard reads see an unchanged pointer and no active job across changed
    artifacts (§7.4).

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

**Ten new tables, two columns on `jobs`, two on `projects`.** Naming follows
`adapters/models.py` conventions (plural snake_case tables, uuid PKs).

### 5.1 Document metadata

```
project_files
  id, project_id → projects.id, name, sha256, size,
  uploaded_by → users.id (nullable), uploaded_at (nullable),
  discovered_at (nullable)
  unique (project_id, name)

file_tags
  id, project_id → projects.id, name
  unique (project_id, name)

file_tag_links
  file_id → project_files.id, tag_id → file_tags.id
  primary key (file_id, tag_id)
```

**`project_files` is metadata, not the source of truth.** `input/` on
disk remains authoritative for existence, and a delete removes the row
along with the file. What is listed is therefore **not** the FS scan
alone: the enumeration set is `input/` ∪ the baseline's filenames (§6.1),
because after a normal delete the baseline is the only record that the
name ever existed — and `removed` is precisely that case.

`sha256` is computed during the existing streaming write in
`services/files.py::save_file` — the bytes already pass through that loop,
so hashing costs no extra disk read.

**Provenance is nullable because migration cannot invent it.** Files that
predate this design have no `project_files` row at all, not merely a
`NULL` hash: nothing on disk records who uploaded them or when. So
`uploaded_by` and `uploaded_at` are nullable, and the first listing that
sees an untracked file in `input/` **discovers** it — inserting a row with
the computed `sha256`, `uploaded_by = NULL`, `uploaded_at = NULL`, and
`discovered_at = now()`. The UI renders an unknown uploader as "—" rather
than attributing the file to whoever happened to open the page. Discovery
is idempotent and runs under the same project lock as any other
`project_files` write.

### 5.2 Index snapshots

```
index_snapshots
  id, job_id → jobs.id, project_id → projects.id,
  kind ('start' | 'baseline'), created_at,
  attributable_titles (json),  -- filenames recovered from
                               -- documents.parquet at capture time
  title_recovery ('available' | 'unavailable_title_column')
  unique (job_id, kind)

index_snapshot_entries
  snapshot_id → index_snapshots.id, name, sha256
  primary key (snapshot_id, name)

projects
  + baseline_snapshot_id → index_snapshots.id (nullable)
  + artifact_epoch (int, not null, default 0)
      -- incremented in the enqueue transaction of every index/update
      -- job, promoted or not; the generation guard's ABA defence (§7.4)
```

**Rows are per (job, kind), not two per job.** `unique (job_id, kind)` —
every `index`/`update` job gets a `start` row before the CLI spawns; a
`baseline` row exists only for jobs that met the promotion conditions
(succeeded, and for `update` a previous baseline must already exist).
Failed, cancelled and non-promoting jobs keep just their `start` row,
which survives as forensic evidence of what the indexer was handed.

**Lifecycle.** `start` is written in its own transaction before the
subprocess spawns. `baseline` is written **in the same transaction that
marks the job `succeeded`** and moves `projects.baseline_snapshot_id`, so
a crash between the two cannot leave a project pointing at a baseline for
a job that never finished. `failed`, `failed(interrupted)` and `cancelled`
jobs promote nothing.

Their `start` rows are pruned by the retention sweep, which is a **new
responsibility** for it, not existing behavior:
`services/retention.py:1-3` states "DB rows are never deleted — history
and the error tail in `jobs.error` survive; only files are reclaimed."
That invariant protects job *history*, and a snapshot of superseded input
hashes makes no historical claim, but the sweep's contract and docstring
change and the change needs its own transactional test. The snapshot
referenced by `projects.baseline_snapshot_id` and the `start` row of the
job that produced it are never pruned.

`projects.baseline_snapshot_id` makes "the current baseline" one lookup
instead of a max-by-timestamp query, and makes the promotion a single
atomic pointer move.

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

**(b) Input freeze, serialized by a project-row lock.** A previous draft
said the freeze was "a single lookup, not a lock protocol". That was
wrong. `jobs_one_active_per_project` is a partial unique index on `jobs`;
it serializes job against job and nothing else. A bare check-then-act
loses this interleaving:

```
T1 upload   check: no active job  ─┐
T2 enqueue                          ├─ commits an index job
T3 runner                           ├─ captures the start snapshot
T4 upload   os.replace lands      ─┘   ...after the snapshot
```

The snapshot again fails to be the indexer's fixed input.

**Both sides take the same project-scoped lock**:
`SELECT ... FROM projects WHERE id = :id FOR UPDATE`.

- **Job enqueue** takes it, inserts the job, and commits.
- **File mutation** streams to the temp file *without* the lock (an upload
  can be arbitrarily long and must not block enqueue for its whole
  duration), then opens the committing transaction: take the lock,
  **re-check** for an active job, write the audit row and `project_files`,
  `os.replace`, commit. `delete_file` and bulk delete do the same around
  their `unlink`.

The re-check under the lock is what closes the race: after enqueue
commits, every subsequent mutation sees the active job and 409s; a
mutation already holding the lock makes enqueue wait until its rename has
committed. The filesystem operation sits inside the locked transaction in
the position the existing code already uses (flush → FS op → commit), so
the accepted residual is unchanged (spec A1: a commit failure after the
rename loses the audit row).

The freeze predicate is **`index` and `update` jobs only**. A `test_run`
reads `output/`, never `input/`, so blocking uploads during a batch would
be ceremony without a reason. The job mutex (§7.3) still keeps an index
from starting during a test run, so (a) is unaffected.

**(c) Baseline advancement**, on success only. Advancement is *not* a
function of filenames alone: GraphRAG compares `documents.title`, so
whether a file was actually ingested this run is only knowable through
the title recovery of §6.3.

| Precondition | New baseline |
|---|---|
| job type `index` | the `start` snapshot, **wholesale** — a full rebuild really does replace the index |
| `update`, **no previous baseline** | **none written.** Nothing is learned: with no prior state, post-run attributability cannot say what *this* run ingested |
| `update`, recovery unavailable at either capture | **entries unchanged, row and pointer still advance** (see below) |
| `update`, recovery available | per-name rule below |

Both snapshot rows carry `attributable_titles` and `title_recovery`,
evaluated against `documents.parquet` and `settings.yaml` at the moment
they are written: the `start` row holds the pre-run set, the `baseline`
row the post-run set that every later listing reads (§6.3). So for
`update` with a previous baseline and available recovery, let `pre` = the
`start` row's set. For each name `N` with hash `H` in the `start`
snapshot:

- `N` **not in** the previous baseline → add `N → H`.
- `N` **in** the previous baseline → advance to `H` **iff `N ∉ pre`**.
  Otherwise keep the old hash.

The condition is `N ∉ pre` alone. `pre` says whether upstream already
knew this title and therefore skipped it; `post` says whether the
attempt succeeded, which is what decides `indexed` versus `skipped` — it
is not part of deciding whether the hash advances. Requiring
`N ∈ post` as well would strand a document that was offered and dropped
**again**: content repaired, upstream retried it (its title was still
unknown), dropped it once more, and the baseline kept the stale hash so
the UI said `modified` when the truth was `skipped`.

Names in the previous baseline but absent from the `start` snapshot are
kept — they are the `removed` files, still live in the index.

**"Unchanged entries" never means "unchanged baseline row".** Advancement
of *hashes* requires recovery available at **both** capture points: `pre`
cannot be computed without it, so there is nothing to compare. But the
run still happened and the artifacts still moved, so a successful
`update` always writes a new `baseline` row and moves
`projects.baseline_snapshot_id` — carrying the entries forward verbatim
while recording the **new** `title_recovery` and `attributable_titles`.

Leaving the pointer on the old row would be the subtle failure: index a
project cleanly, later switch on `title_column`, run a successful
`update`, and a stale pointer would keep reporting `available` with the
*old* title set, so `skipped` would be computed against artifacts that no
longer match it and the overview would never ask for the full index that
would repair it.

This is what makes it a mirror rather than a hedge:

| Case | Upstream | Baseline | State after |
|---|---|---|---|
| content changed, same name | not re-ingested (title already known) | old hash kept | `modified` ✓ |
| deleted | still in the index (`deleted_inputs` discarded) | entry kept | `removed` ✓ |
| previously `skipped`, now fixed | title was unknown → **ingested** | advances (`∉ pre`) | `indexed` ✓ |
| previously `skipped`, edited, dropped again | retried (title still unknown), dropped | advances (`∉ pre`) | `skipped` ✓ |
| previously `skipped`, untouched | retried, dropped | advances (no-op) | `skipped` ✓ |
| brand new, ingested | ingested | added | `indexed` ✓ |
| brand new, silently dropped | not ingested | added | `skipped` ✓ |

Rows three and four are the ones earlier rules got wrong: a filename-only
condition pinned a repaired document at `modified` forever, and adding
`N ∈ post` to the condition did the same to a document that was retried
and dropped again.

**Baseline bootstrap.** Projects indexed before this ships have no
baseline, so every file reads `new` until a **full `index`** — and per the
table above an `update` will not create one, so the requirement is
enforced by the rule rather than merely documented. Stated in the release
notes and surfaced by overview action card 3 (§9.3).

### 5.3 Question sets, runs, ratings

```
question_sets
  id, project_id → projects.id, name, created_by → users.id,
  created_at, archived_at (nullable)

questions
  id, set_id → question_sets.id, lineage_id, text, position,
  created_by → users.id, created_at, archived_at (nullable)

test_runs
  id, project_id → projects.id, set_id → question_sets.id,
  job_id → jobs.id, index_job_id → jobs.id (nullable), method,
  workspace_config_revision (nullable),  -- framed digest of
                                -- settings.yaml + .env, captured with
                                -- the config load (§7.2)
  started_at (nullable), finished_at (nullable)

test_results
  id, run_id → test_runs.id, question_id → questions.id,
  position, question_text,
  answer (nullable),
  citations (json, nullable),  -- Sources entries carry source_name,
                               -- resolved at run time (§7.4)
  timings (json, nullable), error (nullable),
  completed_at (nullable)
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

**A question set referenced by a run is never hard-deleted.**
`question_sets.archived_at` soft-deletes it; the set disappears from the
picker while its runs stay readable. A cascade would destroy exactly the
history the previous paragraph exists to protect.

**The run's manifest is materialized at enqueue, not at execution.**
Between `POST /test-runs` and the worker claiming the job, the set can be
edited — questions added, reworded, archived. If the worker read the set
when it started, the launch dialog's question count, `progress.total`, and
what actually ran could all disagree; reading per question would mix
versions inside one run.

So `POST /test-runs` writes, **in the same transaction as the job insert
and under the same project lock**: the `test_runs` row, and one
`test_results` row per question with `position`, `question_id` and
`question_text` filled and `completed_at = NULL`. Those rows *are* the
manifest: ordered, immutable, and already the thing the worker fills in.
`progress.total` is their count. A cancelled run simply leaves the
remainder with `completed_at = NULL`, which the matrix renders as not run
rather than as an empty answer.

Everything the worker fills in is therefore **nullable by construction** —
`answer`, `citations`, `timings`, `error`, `completed_at`, and
`test_runs.started_at`/`finished_at`. A placeholder row must be insertable
honestly; a non-null sentinel (empty string, `{}`) would be
indistinguishable from a question that genuinely returned nothing.

**Question edits take the same project lock.** The "immutable once
referenced" rule is a check-then-act just like the input freeze: a `PATCH`
could find a question unreferenced, pause, let `POST /test-runs` commit a
manifest referencing it, and then edit it in place — the exact violation
the rule exists to prevent. `PATCH` and `DELETE` on a question or question
set take `SELECT projects ... FOR UPDATE`, re-check references inside the
lock, and commit there. This gets the same barrier test as the freeze
(§10).

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

- `params` (JSON, nullable) — a `test_run` job stores `{run_id}`. The
  ordered question manifest lives in the `test_results` rows written by
  the same transaction (§5.3), not in `params`: a set id would let the
  worker read a set that has changed since enqueue. `argv` stays what it
  is, a graphrag CLI argument vector, and is empty for job types that
  spawn no CLI.
- `progress` (JSON, nullable) — `{done, total}`, written by the test-run
  service between questions so the UI can show batch progress without a
  new streaming mechanism.

## 6. Per-file index state

### 6.1 The enumeration set

**The rows are `input/` filenames ∪ baseline filenames**, not the `input/`
scan alone. A deleted file is exactly the case where the filesystem has
nothing and `project_files` has nothing (§7.1 removes the row with the
file), so an FS-driven listing could never produce a `removed` row and
the state, the health count and overview card 4 would all be unreachable.
The baseline is the only thing that still remembers the name, so it must
be part of the enumeration.

A `removed` row therefore has no file behind it. `size`, `modified_at`
and `sha256` are **null** for it, and the existing `FileEntryOut`
(`api/files_routes.py:35`, currently `size: int`, `modified_at: str`)
widens to nullable — a contract change to an existing response, listed in
§8. Nulls are the honest encoding: inventing a zero size or the deletion
timestamp would let the UI sort and total them as if they were files.
Tags are likewise empty; they lived on the `project_files` row that the
delete removed.

`files.total` counts the union, so a project with three deleted documents
does not silently shrink. Quota usage stays filesystem-derived and is
unaffected.

**A `removed` row is not a file, and no file operation applies to it.**
Preview, delete, tag/untag and bulk selection all exclude it — there is
nothing on disk to read and no `project_files` row to attach metadata to,
so those routes return 404 for such a name and the UI offers no
affordance. The only action it carries is the one that resolves it: run a
full `index`.

### 6.2 The four states that always hold

A pure function in `domain/files.py`, no I/O:

```python
def index_state(
    name: str,
    current_sha: str | None,          # None when the file is gone from input/
    baseline: Mapping[str, str],      # name -> sha256, the current baseline
    attributable: AttributableTitles, # available(filenames) | unavailable(reason)
) -> FileIndexState
```

| State | Rule |
|---|---|
| `new` | not in baseline |
| `modified` | in baseline, hash differs |
| `removed` | in baseline, absent from `input/` |
| `indexed` | in baseline, hash matches |

These four need only the baseline. They are always computable.

### 6.3 `skipped` is a refinement, and it is optional

`skipped` — in the baseline with a matching hash, yet **not among the
filenames attributable to the indexed artifacts** — means graphrag was
handed the file and did not ingest it. It is the only state that points the user at the job log,
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
2. A title matching a **candidate filename** exactly wins. This is
   checked first, so a single-row `report (1).csv` is not mangled by
   rule 3.
3. Otherwise strip a trailing ` (N)` and accept the result only if it
   matches a candidate filename.
4. Otherwise the title maps to nothing.

We never write `title_column` ourselves — `adapters/workspace.py:103-105`
sets only `input.type` and `input.file_pattern` — but `SettingsPanel`
lets a user hand-edit `settings.yaml`, so rule 1 is reachable and must be
detected rather than assumed away.

The **candidate filenames** are the snapshot's own entry names — at
`start` capture, the previous baseline's entries ∪ the `start` snapshot's
names — not a live `input/` listing. A live listing would drop names whose
files are gone, and those are exactly the documents that stay in the index
after a delete (§5.2c); a citation into one of them must still resolve to
its filename so the UI can say the document was removed (§7.4).

**Recovery is evaluated when the artifacts are produced, never at read
time.** Reading `documents.parquet` and `settings.yaml` during a listing
binds the answer to *today's* configuration while the titles were written
under yesterday's. Index a project with `title_column`, then remove the
setting without rebuilding: rule 1 no longer fires, the arbitrary row
titles match no filename, and every baseline file reports `skipped` — a
screen of false alarms produced entirely by a config edit.

So the **baseline snapshot** carries both halves of the answer, captured
at promotion:

```
index_snapshots.attributable_titles  -- filenames recovered, or []
index_snapshots.title_recovery       -- 'available' | 'unavailable_title_column'
```

`title_recovery` exists precisely so an empty list cannot mean two things:
"the rule ran and matched nothing" and "the rule could not run" are
different facts and are stored as different fields.

Listing therefore **does not read `documents.parquet` at all** — it reads
the baseline row. That removes a duckdb read from every file listing as
well as the coupling.

`GET .../files` returns

```
ingest_check: "available"
            | "unavailable_no_baseline"    // nothing indexed yet
            | "unavailable_not_indexed"    // baseline exists, output/ is gone
            | "unavailable_title_column"   // artifacts built under title_column
```

`unavailable_not_indexed` is a cheap `stat` on `output/documents.parquet`,
not a read — it is how the "baseline claims files are indexed but the
output is **gone**" fault (§7.5, overview card 2) is detected. A `stat`
proves existence and nothing more: a present-but-corrupt parquet is
outside what this detects, and the guarantee is worded as missing output,
not healthy output. Corruption surfaces where it already does — the query
path's `QueryError("search", ...)` on a failed frame load.

When it is not `available`, **`skipped` is never emitted** and the UI says
that silent-skip detection is off, and why. An earlier draft's "no parquet
→ empty title set" would have marked every baseline file `skipped` on a
project whose output was missing — turning a diagnostic into noise.

`skipped` is therefore the fifth value of the state field, emitted only
when the check is available. The availability flag lives on the response,
not on every file row: it is a property of the artifacts, and repeating it
per file would invite the UI to render it per file.

**Input configuration is frozen for the duration of a job — including
`.env`.** `services/settings.py::write_settings` (`settings.py:60`) takes
no project lock, so `settings.yaml` can be rewritten between the `start`
capture, the CLI's own read, and promotion — three different
configurations inside one run.

Freezing `settings.yaml` alone is not enough. graphrag runs strict
`string.Template` substitution over `settings.yaml` **before** parsing it,
against `os.environ` overlaid by the workspace `.env`; the existing
validator mirrors that order deliberately (`settings.py:84-90`). So
`input.title_column: ${TITLE_COLUMN}` resolves through `.env`, and
`services/env_file.py::set_env_key` (`env_file.py:102`) and
`delete_env_key` (`env_file.py:124`) take no lock either. An unfrozen `.env` moves the effective configuration
just as a settings edit does, only invisibly.

`write_settings`, `set_env_key` and `delete_env_key` all join the same
project-row lock and the same freeze as file mutation (§5.2b), returning
**409 `project_indexing`** while an `index`/`update` job is active. This
adds a status code to the **existing** routes — `PATCH /{pid}/env` with a
`{key, value}` body (`api/env_routes.py:81`) and
`DELETE /{pid}/env/{key}` (`env_routes.py:100`) — and redesigns nothing;
`SettingsPanel.tsx:135` keeps its `PATCH`. The settings editor and the env
key editor disable saving with the reason shown, and the release note
covers all three alongside the upload freeze.

## 7. Backend changes

### 7.1 Files service

`services/files.py`:

- `save_file` streams to the temp file **outside** any transaction, then
  opens the committing transaction: `SELECT projects ... FOR UPDATE`,
  re-check the input freeze, audit row + `project_files` upsert → flush →
  `os.replace` → commit (§5.2b). Holding the lock across the whole upload
  would block job enqueue for the duration of a large file; taking it only
  for the rename keeps the window short while still serializing.
- `delete_file` and `bulk_delete` take the same lock around their
  re-check, audit row, `project_files` removal and `unlink`.
- Untracked files found by `list_files` are **discovered** into
  `project_files` under the same lock (§5.1).
- `list_files` enumerates `input/` ∪ the baseline's filenames (§6.1),
  left-joins `project_files`, and returns `index_state`, `tags` and
  `sha256` per entry plus the response-level `ingest_check`. It reads the
  **baseline snapshot row**, not `documents.parquet` (§6.3); the only
  artifact touch is a `stat` on `output/documents.parquet` to distinguish
  `unavailable_not_indexed`. `removed` rows carry null `size`,
  `modified_at` and `sha256`.
- New: `add_tags`, `remove_tags`, `list_tags`, `bulk_delete`.
- New: `preview_file(name, *, around=None)` — returns at most
  `PREVIEW_WINDOW_BYTES = 64 * 1024`, decoded UTF-8 with
  `errors="replace"`, off the event loop via `to_thread`. **The cap is on
  the returned window, not on how much of the file may be scanned**: with
  `around` (a passage resolved server-side, §7.4) the file is streamed in
  bounded chunks to locate the first occurrence anywhere in it, and the
  window is centered there. The two claims only conflicted while the cap
  was described as a read limit. With no `around` the window is the head;
  with `around` unmatched, the head plus `match: false`, rather than
  pretending.

### 7.2 Layering: a service owns the batch, an adapter owns I/O

The first design put question iteration, result persistence and
cancellation in `adapters/test_runner.py`. That is use-case and
transaction orchestration, which AGENTS.md places in `services/`.

- `services/test_runs.py` — iterates the question set, writes
  `test_results` and `jobs.progress`, checks cancellation between
  questions, owns the transaction boundary.
- graphrag is reached only through the existing env-shielded
  `adapters/graphrag_search.py`; no new graphrag import site.

**A shared query core is required, but it is not one path for every
answer.** An earlier draft claimed it was; that claim was wrong. There are
two answer-producing implementations and they cannot merge:

- `run_query` (`query.py:102`) → `GraphragSearchAdapter.search`
  (`graphrag_search.py:100`), returning a body plus `context_data`, which
  is what its citations join against. Served by
  `POST /{pid}/query` (`query_routes.py:85`).
- `stream_query` (`query.py:162`) → `GraphragSearchAdapter.stream`
  (`graphrag_search.py:130`), an async generator; streaming returns no
  `context_data`, so its citations join against the very frames handed to
  the adapter. Served by the SSE route (`query_routes.py:106`) — **the
  path the UI actually uses**.

What they genuinely share is the preamble and the tail, and that is what
gets extracted:

```python
async def _prepare_query(project, method, *, config=None) -> Prepared
    # config load (or reuse a caller-supplied one) → frames → frames_ms.
    # No limiter, no user.

async def _execute_query(prepared, method, query, response_type) -> dict
    # search → citations → timings. No limiter.

async def run_query(project, user, ...)      # limiter + prepare + execute
async def stream_query(project, user, ...)   # limiter + prepare + stream + tail
```

`run_query` and `stream_query` keep their exact behavior and their
distinct adapter calls. The batch service calls `_prepare_query` +
`_execute_query`, so it shares configuration loading, frame loading,
citation enrichment and timing assembly with the interactive paths — and
nothing pretends the streaming and non-streaming searches are one
function.

**A run loads its configuration once.** `_prepare_query` accepts a
caller-supplied `config` precisely so `services/test_runs.py` can load it
at worker start and reuse it for every question. `settings.yaml` and
`.env` are frozen only during `index`/`update` (§6.3), so a 200-question
batch that re-read configuration per question could answer its first
questions under one model or prompt and its last under another while
presenting them as one run. Loading once makes the run internally consistent without extending the
freeze to test runs and taking that product cost.

**Recording that configuration honestly takes more than the settings
hash.** `read_settings` (`settings.py:54`) hashes `settings.yaml` bytes
only, but the effective configuration also depends on `.env` — which is
the very reason §6.3 freezes `.env` during indexing. And `read_settings`
and `load_config(root)` (`graphrag_search.py:67`) are two independent
reads, so a naive capture can load configuration A and then record the
hash of configuration B.

So `test_runs.workspace_config_revision` digests **both** files, and the
capture is atomic with the load: the worker takes the project row lock
(§5.2b) **briefly** at start, reads `settings.yaml` and `.env`, computes
the digest, calls `load_config`, and releases. The lock is not held for
the run — only for the read pair.

The digest is a sha256 over a canonical framing, not a concatenation of
two byte strings: for each file in the fixed order `settings.yaml`, `.env`
it absorbs `name || b"\n" || length || b"\n" || bytes`, where a **missing**
file has length `-1` and an **empty** file length `0`. Framing matters —
without a length delimiter, moving a line from the end of `settings.yaml`
to the start of `.env` would produce the same digest, and a deleted `.env`
would be indistinguishable from an empty one.

**The name is `workspace_config_revision`, not `config_revision`, because
it identifies the workspace files and nothing more.** graphrag also
resolves `${...}` against the process `os.environ` (§6.3), which this
digest cannot see and the console does not manage; it changes only on
redeploy or restart. Claiming the field identifies the *effective*
configuration would be claiming more than it can carry. Being a sha256 of
file bytes, it identifies a configuration without exposing any value in
it.

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

Kept deliberately, for two reasons: a test run whose index changes
underneath it is meaningless, so `test_runs.index_job_id` stays honest;
and `FrameCache`'s `(path, mtime, size)` validity key would otherwise
reload parquet files mid-write. It does **not** substitute for the
project-row lock of §5.2(b) — that lock serializes job enqueue against
filesystem mutation, which a `jobs`-table index cannot do. The costs are
real and must appear in the product:
`POST /test-runs` returns **409 `job_conflict`** while *any* job holds the
project — an index, an update, **or another test run**, since the mutex
has no type predicate. The workbench names which job is running rather
than showing a dead button or implying only indexing can block it. The
same applies in reverse on the jobs page.

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
document, and `documents.title` maps back to a filename through §6.3's
rule **as recorded on the baseline** (see below). `Entities`, `Reports`, `Relationships` and `Communities` each
summarize many text units spanning many documents; the UI renders them
unlinked rather than picking one arbitrarily.

Resolution is best-effort throughout: a citation id absent from the frame
(which `build_citations` already treats as normal), a title that maps to
nothing, or `unavailable_title_column` all render unlinked.

**A citation id is only meaningful against the artifacts that produced
it.** `human_readable_id` is assigned per build — `concat_dataframes`
renumbers the delta from the previous maximum, and a full `index` restarts
the sequence — so `Sources (7)` in a run from last week may name a
different text unit today. Resolving a historic citation against current
artifacts would not 404; it would open **the wrong document**, silently.
That is the failure mode §5.3 exists to prevent, arriving through a
different door.

**So `source_name` is resolved while the answer is produced — for every
answer, not only stored runs.** Deferring it to a click has the same bug
in a shorter window: an ad-hoc query answers `Sources (1)` → `file-a`, a
full index lands, the user clicks, and a later lookup opens `file-b`. Ad-hoc
queries are not covered by the job mutex, so nothing prevents that
interleaving.

- `_execute_query` and `stream_query` both attach `source_name` to each
  `Sources` entry as they build citations, against the frames that
  produced the answer. It ships in the `POST /query` body and in the SSE
  `citations` event. There is **no** endpoint that resolves a citation id
  to a filename after the fact — such an endpoint could only ever consult
  current artifacts, which is the bug.
- `services/test_runs.py` gets it for free from the same helper and
  persists it, so `test_results.citations` is self-contained.
- A `source_name` whose file has since been deleted renders as a disabled
  link saying the document was removed, rather than a dead 404.

**The preview locator travels in a request body, not a query string.**
`POST /api/projects/{id}/files/{name}/preview` takes either
`{"result_id", "entry_id"}` (historic — the server reads the stored
passage) or `{"passage"}` (ad-hoc — the client already holds
`entries[].text` from the citations payload). What a body buys is stated
precisely below — it is narrower than "keeps document text private".
`GET .../preview` remains, and returns the head window only.

The ad-hoc form lets a caller search a document for arbitrary text, but
only a document they may already read in full through paged windows, so
it grants no reach they did not have.

**Resolution is not free, and needs its own adapter call.** The query path
loads only the frames in `FrameCache.TABLES` (`frame_cache.py:12`), which
never include `documents`; and `adapters/artifacts.py` reads one
registered table at a time (`artifacts.py:108`), so nothing existing maps
a `document_id` to a `documents.title`. The adapter gains a **batched**
resolver: a set of document ids in, `{document_id: title}` out, one duckdb
read of `documents.parquet`.

**Only `documents` is read fresh.** `text_unit_id → document_id` comes
from the `text_units` frame that answered the query, not from a second
read — `text_units` is loaded for every method that can produce `Sources`
citations (`basic`, `local`, `drift`; `global` loads no text units and so
cites no sources). This is why the resolver takes document ids rather than
text-unit ids.

**The `documents` read is bracketed by a generation guard, because it
cannot be prevented from crossing a rebuild.** Attaching `source_name`
while the answer is produced shortens the window but does not close it:
ad-hoc queries hold no job mutex (§7.3), so a full `index` can land
between the frame load and the `documents` read, and the resolver would
attribute this answer's ids to the *new* build's documents.

The normative sequence, for `_execute_query`, `stream_query` and
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
6. Emit `source_name` only if G0 and G1 report the **same** pointer, the
   **same** epoch, and **neither** reports an active `index`/`update`.
   Otherwise every `source_name` is `null` and the UI renders the
   citations unlinked.

Both reads must be fresh (their own session, no identity-map reuse), and
G1 must follow the `documents` read rather than the frame load: a guard
that closed at step 2 would still let an index start between step 2 and
step 4, which is precisely the interval it exists to cover.

**`artifact_epoch` exists because the pointer is not enough — it is an ABA
hazard.** `IndexRunner` spawns graphrag with `cwd=root`
(`index_runner.py:69`): there is no staging directory and no rollback, so
a job rewrites `output/` in place and a non-zero exit only marks the row
`failed` (`index_runner.py:114`). Failed and cancelled jobs promote
nothing (§5.2), so the pointer does not move. That yields an interleaving
both earlier checks accept:

```
G0   pointer = P, no active job
     frames loaded from build P
     index J starts, rewrites documents.parquet
     J fails; terminal, no promotion
     documents read → J's partial output
G1   pointer = P, no active job          ← identical to G0, artifacts are not
```

`projects.artifact_epoch` is an integer incremented **in the same
transaction that enqueues an `index`/`update` job** — which already takes
the project lock (§5.2b), so no extra synchronization. It is monotonic and
independent of success, so every attempt that could have touched `output/`
moves it, whether it promotes or not. Comparing it across G0/G1 closes the
ABA case that comparing the pointer cannot.

The guard does not stop the race; it detects it and refuses to guess,
which is the only honest option for a path that cannot hold the mutex. The
answer text is always returned — only the links are withheld.

**The resolver returns titles; filenames come from the baseline.** The
adapter reads `documents` and stops there. Turning a title into a filename
is §6.3's rule, and §6.3's whole point is that the rule must be the one
that held when the artifacts were built — so the caller applies the
**baseline snapshot's `title_recovery`**, never today's `settings.yaml`.
When the baseline says `unavailable_title_column`, or there is no
trustworthy baseline, entries stay **unlinked**; the resolver never falls
back to inspecting current configuration or guessing from `input/`.

Without that rule the failure is silent and specific: `data.csv` indexed
under `title_column` emits the title `report.md`, the project also
contains a real `report.md`, the setting is later removed without a
rebuild, and today's rule confidently attributes `data.csv`'s citation to
`report.md`.

**The candidate filenames are the baseline's entry names.** An earlier
draft justified this by saying `attributable_titles` cannot contain a
deleted name; that reason no longer holds, because §6.3's recovery already
matches against snapshot entry names, so `old.md` — deleted from `input/`
but still in `documents` after an `update` — does land in
`attributable_titles`.

The reason that does hold is that `attributable_titles` is a **result of
applying the rule**, not an input to it. Feeding it back in as the
candidate set would make citation resolution circular with the `skipped`
computation that consumes it, and would couple resolution to whether that
computation was available: the field is empty whenever `title_recovery` is
unavailable, so resolution would silently inherit a narrowing it has no
reason to. The entry names are the primary record and cannot narrow.

Citation resolution therefore uses `title_recovery` plus the baseline's
entry names, and **does not read `attributable_titles`**. That is the
precise statement; `attributable_titles` is not single-purpose — the
`start` row's copy is also `pre` in the `update` advancement rule
(§5.2c).

**Call-count contract**, so the test has a failing edge: at most **one**
resolver call per completed question, and it queries only ids not already
in the run's memo. A 200-question run where every question cites the same
five text units issues one call, not 200.

**The historic locator carries an authorization boundary, and it is not
the path project.** `result_id` is a client-supplied identifier for a row
in another table; checking `project:view` on the path project says nothing
about which project *that row* belongs to, and a UUID being hard to guess
is not access control. Left unbound, a stored passage becomes a
cross-project read: hold rights on project A, pass a `result_id` from
project B, and the response is B's document text.

The endpoint therefore verifies three bindings, and **any mismatch is a
404** — never a 403, which would confirm the row exists:

1. `test_results.run_id → test_runs.project_id` equals the path project.
2. `entry_id` names an entry of a **`Sources`** citation on that result.
3. That entry's stored `source_name` equals the path `{name}`.

Binding 3 is what stops the confused deputy: without it a caller with
legitimate rights could pair a real `result_id` with any filename and have
the server search a different document for the stored passage.

**Locator forms are exhaustive, not permissive.** `GET` (no body) → the
head window. `POST {"result_id", "entry_id"}` → historic. `POST
{"passage"}` → ad-hoc. Anything else — one half of the historic pair, or
`passage` mixed with either — is **422**; a partially specified locator is
a caller bug, and guessing an interpretation is how the bindings above get
bypassed by accident.

**The preview locator never travels in a URL.** That is the entire claim,
and it is narrower than an earlier draft's: the historic form is a pair of
ids, but the **ad-hoc form is the passage text itself**, and a request
body can still be buffered by an intermediary. What a body avoids is query
strings, ordinary access logs and the URL length limit. Citations in
general are unaffected either way — the payload has always carried
`entries[].text` (`domain/citations.py:74`).

The ad-hoc body is bounded: `passage` must be non-empty and at most
`PASSAGE_MAX_BYTES = 4096`, since it drives a full-file scan. The bound is
on **`len(passage.encode("utf-8"))`**, validated explicitly — pydantic's
string `max_length` counts characters, so a CJK passage would pass a
character check at three times the byte budget. The request model uses
pydantic `extra="forbid"`, so a mixed or unknown-field body is a 422
rather than a silently ignored key.

### 7.5 Health aggregates

```
GET /api/projects/{id}/health
  files: {new, modified, indexed, skipped, removed, total}
  ingest_check: <as §6.3>
  has_baseline: bool
  last_index: {job_id, type, finished_at} | null
  active_job: {id, type} | null
  latest_run: {run_id, set_id, method, index_job_id,
               ratings: {good, fair, poor, unrated},
               regressions: int} | null
```

`ingest_check` and `has_baseline` are reported together because their
combination carries a fault neither shows alone:
`ingest_check == "unavailable_not_indexed"` **while `has_baseline` is
true** means the project once had output and the file is no longer there —
deleted, or on a volume that did not come back. A file that exists but is
corrupt is not covered (§6.3). Its files still report
`indexed` from the baseline, which is why the overview must call it out
(action card 2, §9.3) instead of scoring the project healthy.

`regressions` is computed server-side (count of lineages whose newest
rating is worse than the previous run's) because the overview must state
it without downloading every result — the first design promised only a
rating distribution, which cannot answer the question the action card
asks.

```
GET /api/projects/health?ids=<uuid,uuid,...>
```

returns the compact subset the project list needs — `files.new`,
`files.modified`, `files.removed`, **`files.skipped`**, **`ingest_check`**,
`has_baseline`, `last_index.finished_at` — one round trip for the whole
list, filtered to projects the caller can see. Without it the list would
issue one request per project.

`skipped` and `ingest_check` are in that subset deliberately: a project
whose only fault is silently dropped documents, or whose output has gone
missing under an existing baseline, is not healthy, and an aggregate that
omitted them would report that it was.

## 8. API contract

`openapi.json` is regenerated and diffed in CI;
`frontend/src/api/types.generated.ts` is regenerated in the same PR
(`npm run gen:types`), including for docstring-only changes.

| Route | Atom | Notes |
|---|---|---|
| `GET /api/projects/{id}/files` | `project:view` | + `index_state`, `tags`, `sha256`; response-level `ingest_check`. **Breaking**: rows are `input/` ∪ baseline, and `size`, `modified_at` **and `sha256`** are all nullable on a `removed` row |
| `POST /api/projects/{id}/files` | `project:edit_content` | **409 `project_indexing`** while an `index`/`update` job is active |
| `DELETE /api/projects/{id}/files/{name}` | `project:edit_content` | same 409 |
| `POST /api/projects/{id}/files:bulk-delete` | `project:edit_content` | same 409 |
| `GET /api/projects/{id}/files/{name}/preview` | `project:view` | head window only |
| `POST /api/projects/{id}/files/{name}/preview` | `project:view` | body `{result_id, entry_id}` or `{passage}` (non-empty, ≤ 4 KiB, `extra="forbid"`) → window centered on the passage, `match` flag; other shapes 422 (§7.4) |
| `POST/DELETE /api/projects/{id}/files/{name}/tags` | `project:edit_content` | not frozen — tags are metadata, not input |
| `PUT /api/projects/{id}/settings` | `project:edit_settings` | **409 `project_indexing`** while an `index`/`update` job is active (§6.3) |
| `PATCH /api/projects/{id}/env`, `DELETE /api/projects/{id}/env/{key}` | `project:edit_settings` | existing routes, unchanged shape; same 409 — `.env` feeds `settings.yaml` substitution (§6.3) |
| (historic locator) | `project:view` | three bindings verified on the `POST` form, any mismatch 404 (§7.4) |
| `GET /api/projects/{id}/tags` | `project:view` | catalog + counts |
| `GET /api/projects/{id}/health` | `project:view` | §7.5 |
| `GET /api/projects/health?ids=` | `project:view` | filtered to visible projects |
| `GET/POST/PATCH/DELETE /api/projects/{id}/question-sets[/{sid}]` | view / `project:edit_content` | questions nested; PATCH follows §5.3 immutability |
| `GET /api/projects/{id}/test-runs` | `project:view` | matrix source; rows keyed by lineage |
| `POST /api/projects/{id}/test-runs` | `project:run_jobs` | enqueues the job + manifest in one transaction; **409 `job_conflict`** while any job holds the project |
| `GET /api/test-runs/{rid}/results` | `project:view` | includes `question_text` |
| `PUT /api/test-results/{id}/rating` | `project:edit_content` | upsert |
| `POST /api/projects/{id}/query`, SSE `citations` event | `project:view` | `Sources` entries gain `source_name`, resolved with the answer (§7.4); null when the generation guard withholds links. No after-the-fact resolution endpoint exists |

Atom choice follows the existing model: reading is `project:view`,
curating content (tags, question sets, ratings) is `project:edit_content`,
spending compute is `project:run_jobs` — the same atom that gates
indexing, for the same reason.

`GET /api/projects/{id}/jobs` gains an optional `type` filter so the jobs
page can exclude `test_run` rows server-side.

Existing contracts that change, all release-noted: `FileEntryOut` gains
nullable `size`, `modified_at` and `sha256` (above), and
`PUT .../settings`, `PATCH .../env` and `DELETE .../env/{key}` each gain
a 409 they never returned before. No route shape changes.

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
- **Frozen state**: while an `index`/`update` job is active, the uploader
  and delete actions
  are disabled with the reason shown, rather than letting the user
  discover the 409.
- **Bulk actions**: delete and tag/untag over selected rows. Bulk delete
  confirms with count and total size — deleting 30 documents is not the
  same act as deleting one.
- **Preview drawer**: props
  `{ name, locator?: { resultId: string; entryId: number } | { passage: string } }`,
  sent as a `POST` body (§7.4). `entryId` is a `number`, matching the
  existing `Citation.ids: number[]` in `frontend/src/api/types.ts:40`; a
  string id would have needed a conversion that exists nowhere else.
  Slice ① never passes a locator; slice ③ does, and picks the variant by
  whether the answer came from a stored run or an ad-hoc query.
- **Upload**: `Upload.Dragger` unchanged, but a persistent "N documents
  not yet indexed" bar appears above the table linking to the jobs page.

### 9.2 Slice ② — Retrieval test workbench

`QueryPanel` stops being a page and becomes the workbench's **ad-hoc
query** mode. Two execution paths, deliberately different:

- ad-hoc → existing SSE stream (interactive, token-by-token)
- batch → `test_run` job (long, cancellable, survives navigation)

Both render through one `AnswerView` (answer + citations + timings). They
do **not** share a search call — SSE keeps `stream_query`, the batch uses
`_execute_query` — but after §7.2 they share configuration loading, frame
loading, citation enrichment (including `source_name`) and timing
assembly, which is what makes the two renderings comparable. If the same
answer rendered differently in two places, users would reasonably suspect
they had gotten different results. An ad-hoc answer saves into a question
set in one action.

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
- **`Citation` is hand-maintained**, not generated: it lives in
  `frontend/src/api/types.ts:34` because the SSE contract has no backend
  `response_model`. It gains `entries[].source_name: string | null`, and
  `null` — the generation guard, an unrecoverable title, or a non-`Sources`
  label — is what renders unlinked. `npm run gen:types` will not do this
  one; it has to be edited by hand alongside the schema change.

### 9.3 Slice ③ — Wiring

- Routed sidebar replaces `Tabs` (§4).
- **Overview page** consumes `/health`. The action card is one ordered
  check, and each card links to its target **with filters already
  applied**:

  1. `active_job` → a job is running; link to its log.
  2. `has_baseline` **and** `ingest_check == "unavailable_not_indexed"` →
     the indexed output is gone while the baseline still claims files are
     indexed; run a **full index**. This outranks everything below it
     because every state under it is being read off output that no longer
     exists.
  3. no `has_baseline` → run a **full index** to establish a trustworthy
     baseline (an update will not do it — §5.2).
  4. `removed > 0` → deleted documents are still answering queries; only
     a **full index** clears them.
  5. `new + modified > 0` → rebuild; link to `files?state=new,modified`.
  6. `skipped > 0` → documents graphrag did not ingest; link to
     `files?state=skipped`.
  7. `latest_run.regressions > 0` → link to the workbench filtered to
     regressions.
  8. otherwise healthy.

  `removed` outranks `new`/`modified` because it is the only one that
  needs a *full* index rather than an update, and an earlier draft omitted
  it entirely — a project whose only problem was deleted documents
  reported itself healthy. Rule 2 exists for the same class of mistake:
  missing output is not the absence of a problem.

  When `ingest_check == "unavailable_title_column"`, rules 4-6 still
  apply but the card adds that silent-skip detection is off, so `skipped`
  is not evidence of health either way.
- **Citation loop closes**: `AnswerView`'s `Sources` citations become
  clickable — using the `source_name` that arrived **with the answer**,
  never a later lookup — and open `FilePreviewDrawer` with the locator for
  their origin (§7.4): `{resultId, entryId}` for a stored run,
  `{passage}` for an ad-hoc query. Slice ①'s reserved prop is used here.
- **Project list** gains an index-health column fed by
  `GET /api/projects/health?ids=`.

### 9.4 i18n

Every new string lands in both `zh-TW` and `en-US` in the same PR;
`i18n.test.ts` enforces parity. The five index states, three
`ingest_check` reasons, three rating levels, and the two 409 conditions
are user-facing vocabulary — they get real sentences in both locales, not
labels transliterated from the schema.

## 10. Testing

Backend:

- `domain/files.py::index_state` — table-driven over all states plus
  boundaries: never indexed, empty baseline, `NULL` sha256 from the
  backfill path, and every `ingest_check` value (asserting `skipped` is
  unreachable when unavailable).
- **`artifact_epoch`** — incremented by every `index`/`update` enqueue
  including ones that later fail or are cancelled, never by a `test_run`,
  and never decremented.
- **Baseline advancement** — one case per row of the §5.2(c) mirror
  table, plus: `update` with no previous baseline writes **no** baseline;
  `update` under `unavailable_title_column` leaves the baseline
  **entries** unchanged while still writing a new `baseline` row,
  advancing `projects.baseline_snapshot_id`, and recording the new
  recovery provenance; a `start` row is written for every job while a
  `baseline`
  row and the `projects.baseline_snapshot_id` move happen only on
  `succeeded`, in one transaction; `failed`/`cancelled` promote nothing.
- **Input freeze** — upload/delete/bulk-delete return 409 while an
  `index`/`update` job is queued or running, and succeed once it is
  terminal; a `test_run` job does **not** freeze them.
- **Freeze race, with a barrier** — the point of the lock is an
  interleaving, so the test must produce it: park an upload after its
  stream completes but before its committing transaction, run enqueue to
  completion, release the upload, and assert it returns 409 and its bytes
  never reached `input/`. Then the mirror case: park an upload holding the
  lock mid-rename, start enqueue, assert enqueue blocks until the upload
  commits and the `start` snapshot then contains the new hash. A test that
  only asserts "active job → 409" passes against the broken design.
- **Run manifest immutability** — editing, adding and archiving questions
  between `POST /test-runs` and the worker claiming the job changes
  neither the executed questions nor `progress.total`; a cancelled run
  leaves the remaining rows `completed_at = NULL`.
- **Question edit race, with a barrier** — the park point must be one a
  *correct* implementation can reach. Parking a `PATCH` after its
  reference check and letting `POST` commit describes the broken design:
  under the lock protocol the check is already inside the lock, so `POST`
  would block. The two legal interleavings:
  - `POST` holds the lock with the manifest not yet committed; `PATCH`
    blocks, then on release re-checks, finds the reference, and forks a
    new lineage row.
  - `PATCH` holds the lock with the edit not yet committed; `POST` blocks,
    then on release materializes the manifest with the **new** text.
- **Question-set delete** — a set referenced by a run archives instead of
  cascading, and its runs stay readable.
- **Discovery** — an untracked file in `input/` gains a `project_files`
  row with a computed hash and NULL provenance, idempotently.
- **Title recovery** — exact-match precedence over ` (N)` stripping
  (a single-row `report (1).csv` must not become `report`), and
  `title_column` forcing `unavailable_title_column`.
- **Recovery provenance** — a project indexed under `title_column` whose
  setting is later removed still reports `unavailable_title_column` and
  emits no `skipped`, because listing reads the baseline row rather than
  today's `settings.yaml`; and `attributable_titles = []` with
  `title_recovery = 'available'` is distinguishable from unavailable.
- **Config freeze** — `PUT .../settings`, `PATCH .../env` and
  `DELETE .../env/{key}` each return 409 while an `index`/`update` job is
  active, and the barrier case: a write parked before its commit cannot
  land after enqueue captured the `start` snapshot. The `.env` case needs
  its own test — a `settings.yaml` referencing `${TITLE_COLUMN}` changes
  meaning through `.env` alone.
- **Baseline row always advances on a successful `update`**, even when
  recovery is unavailable and the entries carry forward verbatim: a new
  `baseline` row exists, `projects.baseline_snapshot_id` moves, and the
  stored `title_recovery` reflects the **new** configuration. The
  regression this guards: index cleanly → enable `title_column` → update
  → listing must stop reporting `available`.
- **Removed rows** — after a normal delete the name still appears with
  `index_state = "removed"` and null `size`/`modified_at`/`sha256`;
  `files.total` counts it; it disappears after a full index.
- A **slow test** against a real indexed workspace covering `text`,
  multi-row CSV, multi-row JSON, and a `title_column` project — the rule
  in §6.3 is read off upstream source and must be pinned against upstream
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
- `/health` `regressions` arithmetic; the `has_baseline` +
  `unavailable_not_indexed` fault surfacing as action card 2; `?ids=`
  filtered to visible projects and carrying `skipped`/`ingest_check`.
- `preview_file` — a match beyond the first 64 KiB is still found and
  centered; an unmatched passage returns the head with `match: false`;
  both locator forms resolve; a `removed` name 404s.
- **Historic citations never re-resolve** — run A cites `Sources (1)` →
  `file-a`; rebuild so current `Sources (1)` → `file-b`; opening run A's
  citation must still reach `file-a`, and must never reach `file-b`. This
  is the negative test that catches a resolver quietly falling back to
  current artifacts.
- **Historic locator bindings** — each returns 404, never 403 or content:
  a caller with rights on project A passing a `result_id` from project B;
  a valid `result_id` paired with a `{name}` other than the entry's stored
  `source_name`; an `entry_id` belonging to a different result or to a
  non-`Sources` citation.
- **Locator forms** — `GET` → head; `POST {result_id, entry_id}` →
  historic; `POST {passage}` → ad-hoc; every other body shape, including
  half a historic pair, → 422.
- **Passage bound is bytes** — 4096 ASCII bytes accepted, 4097 rejected,
  and a multi-byte UTF-8 passage under 4096 *characters* but over 4096
  *bytes* rejected.
- **Batched source resolution** — at most one resolver call per completed
  question, querying only ids absent from the run's memo: 200 questions
  citing the same five text units issue exactly one call.
- **Resolver provenance** — `data.csv` indexed under `title_column`
  emitting the title `report.md`, alongside a real `report.md`, with the
  setting later removed: the citation must render **unlinked**, never
  attributed to `report.md`.
- **Ad-hoc citations do not re-resolve either** — answer an ad-hoc query
  citing `Sources (1)` → `file-a`, rebuild so current `Sources (1)` →
  `file-b`, then open the citation from the still-rendered answer: it must
  reach `file-a`. Same shape as the stored-run test, different door.
- **Generation guard, with a barrier** — park an ad-hoc query between its
  frame load and its `documents` read, promote a new baseline, release:
  the answer must still be returned and every `source_name` must be
  `null`. A test that only rebuilds *after* the response passes without
  the guard existing. Second case, which a G1-at-frame-load
  implementation fails: start an `index` in that same interval without
  promoting a baseline — G1 must see the active job and withhold links.
  **Third case, the ABA control, which pointer-and-active-job comparison
  alone cannot catch**: in that same interval let an `index` run to
  completion **and fail**, rewriting `documents.parquet` on its way; at G1
  the pointer is unmoved and no job is active, yet every `source_name`
  must still be `null` — only `artifact_epoch` distinguishes it.
- **Citations into removed documents stay linkable** — full-index
  `old.md`, delete it, `update`, then cite the still-indexed document:
  `source_name` must be `old.md` and the UI must render it as a disabled
  "document removed" link, not as an unresolved citation.
- **Config revision capture** — editing `settings.yaml` **or** `.env`
  between the worker's config load and its provenance write cannot make
  `workspace_config_revision` describe a configuration the run did not
  use; and the framing distinguishes a missing `.env` from an empty one,
  and a line moved between the two files from one that was not.
- **One configuration per run** — editing `settings.yaml` or `.env`
  between two questions of a running batch does not change which
  configuration the later questions use, and
  `test_runs.workspace_config_revision` records the one that was loaded.
- A stored `source_name` whose file was deleted renders disabled rather
  than 404-ing the drawer open.
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
  briefly noisy, self-correcting, and surfaced as action card 3 (§9.3)
  rather than left for the user to infer.
- **The input freeze is a behavior change** for existing endpoints:
  uploads and deletes during an `index`/`update` job used to succeed and
  now 409. That is the point — they were silently corrupting the index
  state — but it is visible and belongs in the release notes. The freeze
  is scoped to `index`/`update`: a `test_run` reads `output/` only, so it
  does not block document work, and the release note says so rather than
  leaving users to infer it from a job type they cannot see.
- **`PUT .../settings`, `PATCH .../env` and `DELETE .../env/{key}` gain a
  409** while an `index`/`update` job runs.
  Editing configuration mid-run produced artifacts whose provenance no
  single configuration described; the freeze is what makes §6.3's stored
  recovery flag meaningful. Visible change, release-noted.
- **`FileEntryOut.size`, `modified_at` and `sha256` become nullable**, and
  listings can contain names with no file behind them. Clients that assumed a file per
  row need the `index_state` check; the generated types make this a
  compile error rather than a runtime surprise.
- **The project-row lock is on the hot path** for uploads, deletes,
  settings writes, `.env` edits, question edits and job enqueue. It is held only across the committing transaction (never across
  the upload stream, §7.1), and contention is per project, so concurrent
  work on different projects is unaffected. Two users uploading to the
  same project serialize at the rename, which is the correctness they are
  paying for.
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
- Release notes cover four visible changes: the input freeze on
  upload/delete, the new freeze on `PUT .../settings`, `PATCH .../env`
  and `DELETE .../env/{key}`, the nullable `FileEntryOut.size`/`modified_at`/`sha256` with
  rows that have no file behind them, and the requirement that a
  trustworthy baseline comes from a full `index` rather than an `update`.
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
