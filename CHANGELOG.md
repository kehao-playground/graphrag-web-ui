# Changelog

Notable changes to GraphRAG Web UI, newest first. Releases are git tags
(`v0.1.0` is the first); within a release, entries are grouped by the
feature slice that shipped them, with the date the slice landed on
`main`. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

## [0.1.1] — 2026-10-06

A patch release for two dependency advisories published after 0.1.0.
Upgrade from 0.1.0 by rebuilding both images; no configuration,
migration or API change.

### Security

- multidict 6.8.0 → 6.9.1 (CVE-2026-104874: a remotely driven memory
  leak in items-view set operations; transitive, via aiohttp/litellm).
- source-map-js 1.2.1 → 1.2.2 (event-loop denial of service through
  indexed source-map section offsets; build-time, via vite/postcss and
  jsdom).

### Changed

- CI: a `slow` workflow runs the six real-graphrag tests with a model key
  weekly, on `main` after a backend dependency change, and on demand
  (`gh workflow run slow --ref <branch>`); it fails on any skipped test.
- The README screenshots are recaptured on this release (the upload hint
  lists extensions with the locale's separator).

## [0.1.0] — 2026-10-05

The first tagged release: everything from the foundation to the quality
review below.

### Toolchain and dependencies (2026-10-05)

- **Node 26** everywhere: the web image, CI and the local-development
  docs (was 24).
- **graphrag 3.2.0** (was 3.1.2), with litellm 1.100.1 and lancedb 0.38.
  The settings keys the app writes are unchanged. 3.2.0 adds an opt-in
  `sqlite` cache; its `cache.database_name` must be a bare file name,
  like every other path setting confined to the workspace (400
  `settings_path_escape`).
- SQLAlchemy 2.1, FastAPI 0.142, Starlette 1.7, uvicorn 0.54, and patch
  bumps across both stacks. No API contract change.

### Quality review — fix waves F1–F37 (2026-09-22 → 2026-10-02)

A four-part review (architecture, correctness and security, functionality
and operations, UX) produced 268 findings; 266 are fixed, and 2 were closed
as won't-fix (full record: `docs/superpowers/reviews/backlog.md`).
Operator- and API-facing changes:

- **Breaking — graphrag configuration trust boundary.** `${VAR}` placeholders in
  a project's `settings.yaml` resolve from that project's `.env` **only**, never
  from the API process environment. The indexing subprocess gets an allowlisted
  environment, and process-level names (`PATH`, `PYTHON*`, `*_PROXY`, CA bundles)
  are refused as `.env` keys. Storage, cache, reporting, vector-store and prompt
  paths must stay inside the workspace (400 `settings_path_escape`), and
  `input.type` is locked at creation (400 `settings_input_locked`). **Ops
  action:** rotate `JWT_SECRET` in every deployment that has had non-admin users.
- **Breaking — startup checks.** The API refuses to start when
  `BOOTSTRAP_ADMIN_PASSWORD` is the `.env.example` placeholder or shorter than
  12 characters.
- **Breaking — API contract.** The jobs list and settings versions are paged
  (`limit`/`offset`) and answer `{items, total}`. The jobs list leaves out
  `test_run` jobs unless asked (`?type=`). Error bodies carry a `code`, 422s
  are `{detail, code: validation_failed}`, and operationIds are the handler
  names. Regenerate any client built from `openapi.json`.
- **Sessions.** Refresh-token rotation is atomic. A sign-in's refresh chain ends
  after 30 days, however often it is renewed (alembic migration). Live streams
  sign in with a one-minute single-stream ticket. `ACCESS_TOKEN_MINUTES` and
  `REFRESH_TOKEN_DAYS` are documented.
- **Indexing.** A project's first index no longer reports every file as
  `skipped`. Citations link to documents on every query path. Running jobs
  show workflow progress, and job logs follow the output live, with
  auto-scroll that pauses and reconnects. A placeholder API key is flagged
  before indexing. A job is refused when the project is over quota, and
  *Clear cache* empties graphrag's cache.
- **Audit.** Every state-changing route writes an audit row. New actions:
  `job.enqueued`, `job.cancelled`, `user.password_changed`.
- **Deployment.** Uploads above 1 MiB work through the shipped nginx and the
  Helm ingress. The proxy-auth overlay boots. The default Helm install pulls
  a working PostgreSQL image. `/api/ready` answers 503 when a check fails.
  Compose services restart on their own, and the api has a health check.
  The Helm chart gains a startup probe and storage-class and existing-claim
  options. Tokens are redacted from access logs. The README gains an
  *Operations* section covering upgrades, backup/restore, logs, sizing and
  session lifetimes.
- **Interface.** Question sets can be created, renamed and archived from the
  UI. Settings form mode shows the model fields. The overview names the next
  action. zh-TW terminology is consistent throughout. Many smaller UX fixes
  come from two full walkthroughs in both languages.

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

[0.1.1]: https://github.com/kehao-playground/graphrag-web-ui/releases/tag/v0.1.1
[0.1.0]: https://github.com/kehao-playground/graphrag-web-ui/releases/tag/v0.1.0
