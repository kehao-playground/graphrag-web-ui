# Changelog

Notable changes to GraphRAG Web UI, newest first. The project has no
version tags yet; entries are grouped by the feature slice that shipped
them, with the date the slice landed on `main`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Knowledge manager — slice 3: citation-to-document loop (2026-09-11)

- `Sources` citations now carry a `source_name`, resolved **with the
  answer** at the moment it is produced — for ad-hoc queries and batch
  runs alike. There is **no endpoint that resolves a citation to a
  document after the fact**: a citation id is only meaningful against
  the artifacts that produced it — a later build renumbers ids, so a
  deferred lookup would silently open the *wrong* document. A
  `source_name` whose file has since been deleted renders as a disabled
  link saying the document was removed, rather than a dead 404.
- The same conservatism governs links after a failed or interrupted
  index: citation links stay off until a successful index promotes a new
  baseline. `/health`'s `artifacts_stale` and the overview page's
  action card ("Indexed output unavailable") are where the user sees why.
- The overview page's action card names the single next action, and the
  project list flags faults (`3 to index`, `Deleted documents still in
  the index`).

### Retrieval testing — slice 2: question sets and test runs (2026-09-09)

- Test runs and index/update jobs are **mutually exclusive per project, in
  both directions**: the one-active-job-per-project rule holds even when the
  global `MAX_CONCURRENT_JOBS` budget is free, and the jobs page names the
  same conflict from the other side (HTTP 409 `job_conflict`).
- A batch run executes **inside the API process**, as interactive queries
  already do: many sequential queries, not a new class of load — but
  sustained, with `MAX_CONCURRENT_JOBS` as the throttle. It deliberately
  bypasses the per-user interactive rate limit, which one 20-question batch
  would otherwise consume outright.
- No new environment variables; the bounds are domain constants
  (`MAX_QUESTIONS_PER_SET`, `MAX_QUESTION_CHARS`, `MATRIX_DEFAULT_RUNS`).
- Tests tab: rating matrix (`good` / `fair` / `poor` plus a note),
  "regressions only" filter, result drawer with `1`/`2`/`3` keyboard
  rating, sentence-level diff between two runs, question versioning.

### Knowledge manager — slice 1: document governance (2026-09-07)

- Input freeze: uploads, single/bulk deletes, `PUT .../settings`,
  `PATCH .../env` and `DELETE .../env/{key}` now return **409
  `project_indexing`** while an `index`/`update` job is queued or running.
- `FileEntryOut.size` / `.modified_at` / `.sha256` are **nullable**, and
  listings can contain rows with no file behind them (`removed` documents
  still in the index).
- Documents tab: per-file index state, client-side search and filters,
  tags, bulk delete with a count-and-size confirmation, and a bounded
  document preview (optionally centered on a passage).

### Earlier (2026-08-19 → 2026-09-02)

- Composable roles: accounts hold a set of roles (`user_admin`, `ops`,
  project `viewer`/`maintainer`/`editor`/`owner`, custom roles built from
  permission atoms); AdminRoles page manages the catalog.
- Audit log read-back through `Admin — Audit` (read-only, filterable by
  action and target type).
- `JWT_SECRET` must be ≥ 32 characters and not a shipped placeholder in
  local auth mode; the API refuses to start otherwise (**breaking** for
  deployments that relied on the `.env.example` default).
- Optional `AUTH_MODE=proxy` (oauth2-proxy) with JIT user provisioning —
  see [docs/oauth2-proxy.md](docs/oauth2-proxy.md).
- Bilingual (zh-TW/English) interface.
- Foundation: projects, uploads, index/update jobs with live logs,
  four query modes over SSE, explore tables and WebGL graph view.
