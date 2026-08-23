# Hygiene Remediation Design

Date: 2026-08-23
Status: revised after user code-review (P1–P10); supersedes 9736bea
Input: four read-only audits (architecture conformance, backend smells,
frontend smells, language/docs inventory) + user verification against
main@9736bea.

## 1. Problem

The five feature phases are merged and green (backend 205 fast + 5 slow,
frontend 57, CI green), but four audits found:

1. **Clean-architecture spirit debt** — the codebase conforms to the letter
   of every AGENTS.md §9 rule, but:
   - `api/env_routes.py:83,99` and `api/files_routes.py:107,138` call
     `audit()` add + `db.commit()` directly. Services must own the
     transaction boundary; routes must not commit.
   - `api/users_routes.py:32-48` runs raw `select()` queries;
     `:65,85` two `await db.get(User, ...)` + inline 404s; `:70-76` holds
     the last-active-admin business rule in the handler; `:99`
     (`list_users_brief`) is another raw select.
   - `_ws_path` — the workspace path-safety guard — lives in
     `services/projects.py:30`, calls `get_settings()` (config
     dependency) and `Path.resolve()` (filesystem I/O), and is privately
     imported by 8 services, `services/env_file.py`, AND two api modules
     (`api/jobs_routes.py:26`, `api/dry_run_routes.py:16` — api importing
     a service private is the same class of debt).
   - `query_routes._prepare_query` and `explore_routes._allowed` are
     byte-identical helpers; project-permission scaffolding appears as
     15 direct `can(user.role, ...)` calls + those 2 helpers across 6
     route modules.
2. **Robustness smells** — blocking sync I/O inside async handlers:
   `services/files.py:69` (`_dir_size` rglob, via sync `usage_bytes`
   called without await from `files_routes.py:117`),
   `services/files.py:127-142` (`list_files`: async def but iterdir +
   stat), `services/jobs.py:35-41,85-108` (preflight
   `disk_usage`/`_tree_bytes`), `api/health_routes.py:30-41`
   (`disk_usage`). No schema-drift test between alembic head and
   `Base.metadata`. `frontend/src/api/types.ts` is hand-maintained with
   no drift check against the backend contract (see A5.2 for the real
   shape of that contract). Real-corpus test fixtures: one cross-module
   `noqa` import (`test_real_corpus_explore.py:24` imports from
   `test_real_corpus_query`) plus independently duplicated fixture
   bodies (`test_real_corpus_jobs.py:73,82,98` vs
   `test_real_corpus_query.py:66,75,91`) — the exact patterns behind the
   silent `query_client` breakage in PR #5. `QueryError`/
   `ExploreReadError` parallel hierarchies. Frontend `detailOf` helper
   defined 6× — but NOT six identical copies: 5 return
   `Promise<string>`, `SettingsPanel.tsx:24` returns the full body
   (callers need e.g. 409 `content_hash`).
3. **Language drift** — measured (not estimated): 114 CJK comment lines
   across 22 backend files, ~45 frontend lines, 71 lines across deploy/
   helm/compose/`.env.example`/Dockerfile/pyproject/nginx.conf/ci.yml/
   index.css — **~230 lines total, ~30+ files**. Policy says
   English-authoritative with boy-scout migration on touch; migration
   never catches up with the stock.
4. **Onboarding gap** — no root README (Foundation-B plan recorded
   "README not present — skip"), no quickstart, no runbook. The 15-minute
   happy path exists only inside the zh-TW design spec and per-phase
   plans; a normal reader cannot discover it. The UI is zh-TW but no
   zh-TW operator docs exist (`docs/zh-TW/` reserved in CONTRIBUTING.md,
   never created).

## 2. Goals

- Restore clean-architecture *spirit*: transactions owned by services,
  routes translate only, path guard pure in domain.
- Close the two drift surfaces (DB schema, API types) with generated or
  tested contracts instead of hand-mirroring.
- Remove the known event-loop freeze risk and fixture-duplication
  hazards.
- Give the repo a root README with a verified 15-minute quickstart, and
  a zh-TW mirror of it under `docs/zh-TW/`.
- End the mixed-language comment state: one-time sweep to English plus a
  CI guard so the stock cannot regrow.
- Code comments stay English-only (user decision 2026-08-23: zh-TW
  versions are for *documents*, never comments; UI strings, API error
  details, and operator-facing output (helm NOTES.txt) remain zh-TW by
  design).

## 3. Non-goals (recorded for future feature work)

Explicitly deferred, in writing, so they are not lost:

- Route project-permission scaffolding extraction (15 direct `can()`
  calls + 2 byte-identical helpers) and `_prepare_query`/`_allowed`
  unification.
- `run_query`/`stream_query` shared prelude extraction.
- Subprocess output streaming (init/dry-run currently buffer full CLI
  output via `capture_output=True`; index failure path reads the whole
  log before slicing the tail).
- Upload quota TOCTOU race (pre-write snapshot vs final size under
  concurrent uploads). Note interaction: A4's `to_thread` switch widens
  the race window slightly (extra thread hop between snapshot and final
  size); accepted because it is a probability change, not a correctness
  regression — see §6.
- `services/query.py` split (228 lines, POST + SSE).
- Adding `response_model` to the explore/query endpoints that currently
  return bare dicts (A5.2 ratchets this debt instead; codegen covers
  them once models exist).

## 4. Wave A — code remediation (`feature/hygiene-code`)

TDD throughout; fast suites must stay green; no behavior change is
allowed without a test pinning the new contract first.
Plan file: `docs/superpowers/plans/2026-08-23-hygiene-code.md`.

### A1. Transaction ownership returns to services
Move the `audit()` add + commit blocks from `env_routes.py` (83, 99) and
`files_routes.py` (107, 138) into the corresponding service functions.
Chosen mechanism — service functions take `(session, actor_id, ...)`
(matching the existing `services/projects.py` convention):

- `set_env_key(session, project, key, value, actor_id)`
- `delete_env_key(session, project, key, actor_id)`
- `save_file(session, project, filename, content, actor_id)`
- `delete_file(session, project, filename, actor_id)`

Route call sites (4) and tests calling these services directly are
updated in the same change.

**Ordering correction (decided: yes, fix it now).** Current order is
write-file → audit → commit; if commit fails, the file exists but no
audit row exists. New order follows AGENTS.md: `audit()` add +
`session.flush()` → external work (file write via temp file + atomic
rename) → `commit()`; on any failure, rollback + best-effort unlink of
the temp/renamed file. Residual risk: crash between rename and commit
leaves an un-audited file — accepted and noted here; full compensation
journaling is out of scope.

### A2. `users_routes` stops querying
Move into `services/users.py`: the `select()` statements (32-48), the
two `await db.get(User, user_id)` + 404 decisions (65, 85), the
last-active-admin rule (70-76), and the `list_users_brief` select (99).
The handler keeps only: parse → call service → translate domain error to
HTTP. Line numbers above corrected per user review.

### A3. `_ws_path` splits into pure domain function + thin service wrapper
`_ws_path` is NOT pure today: it calls `get_settings()` and
`Path.resolve()` (real I/O). Moving it wholesale into `domain/` would
violate the stdlib-only discipline the layer currently keeps
(`domain/{artifacts,jobs,citations,permissions}.py` import only
dataclasses/pathlib/re/enum). Split instead, following the existing
`domain/jobs.py:log_path_for(root, job_id)` precedent:

- `domain/workspaces.py` (new): `workspace_path(root: Path, project_id)
  -> Path` — builds the path and asserts containment; pure, no I/O, no
  config.
- `services/` keeps a thin wrapper owning `get_settings()` + `resolve()`
  (public `ws_path`, no underscore), imported by everything that today
  imports `_ws_path`.

Blast radius (corrected): 8 services + `env_file.py` + 2 api modules
(`jobs_routes.py:26`, `dry_run_routes.py:16` — api→service-private
import cleaned in the same change) + 9 test files
(retention/dry_run/explore_api/job_logs_sse/jobs_api/env/settings/files
+ real-corpus suite as needed). `services/projects.py` drops its local
definition outright — clean cutover, no re-export shim.

### A4. Async handlers stop blocking
Criterion (decided): **wrap unbounded tree walks and whole-disk stats
(`rglob`, `iterdir` loops, `disk_usage`) in `asyncio.to_thread`;
fixed-size single-file reads/writes stay synchronous.** The line is
drawn at "runtime scales with workspace size" vs "one bounded syscall".

In scope:
- `services/files.py:69` `_dir_size` (rglob) — called via sync
  `usage_bytes`; `usage_bytes` becomes async (or gains an async wrapper)
  and `files_routes.py:117` awaits it. Call-site change, API shape
  unchanged.
- `services/files.py:127-142` `list_files` — iterdir + stat per entry;
  wrap the directory scan.
- `services/jobs.py:35-41,85-108` — preflight `disk_usage` /
  `_tree_bytes` (async def, sync body — same bug class).
- `api/health_routes.py:30-41` — `disk_usage`.

Explicitly out (bounded single-file ops, left sync, recorded so the next
person knows why): `services/settings.py:46,79`, `services/env_file.py:27,39`,
`adapters/job_logs.py:15` (stat in SSE tail loop), `save_file`'s write
loop.

### A5. Two drift gates

**A5.1 Schema drift.** Reuse the existing session-scoped `migrated_db`
fixture (`backend/tests/conftest.py:34`, already runs
`command.upgrade(cfg, "head")`) — cost is one comparison test, not new
migration infrastructure. Comparison via `alembic.autogenerate.
compare_metadata`, filtered to a fixed category set:
- FAIL on: add/remove table, add/remove column, column type change,
  nullability change.
- IGNORE (known noise sources): `server_default` differences, index and
  constraint *naming*, index/constraint presence where metadata and DB
  disagree only in rendering.
Today, table-level additions are already caught accidentally by
conftest's `TRUNCATE {Base.metadata.sorted_tables}`; this gate's real
increment is the column/type/nullability layer. Note: this is a "fast"
test but still requires Docker (testcontainer) like the rest of the
suite.

**A5.2 API types drift.** The contract is NOT `api/schemas.py` — of
`types.ts`'s 23 exports, only 5 come from `schemas.py` (User/UserBrief/
Job/LastRun/Preflight); the rest of the pydantic models live inline in
8 route modules (`settings_routes.py:28,38,42`, `projects_routes.py:40,
63`, `env_routes.py:24,29`, `files_routes.py:30,35,41`,
`dry_run_routes.py:19`), and 8 more types (`ArtifactPage`,
`ArtifactDetail`, `GraphNode/Edge/Data`, `Citation`, `QueryTimings`,
`QueryMethod`) have NO backend pydantic model at all — explore/query
endpoints return bare dicts. Also `types.ts:47` `JobStatusColor` is a
runtime const, not a type.

Chosen approach — **`app.openapi()` + `openapi-typescript`** (user's
option 1):
- Backend emits `openapi.json` (script or ad-hoc command); generation
  covers every router regardless of which module owns the models, and
  endpoints missing `response_model` surface as `unknown` — the debt
  becomes visible instead of invisible.
- `frontend/src/api/types.generated.ts` is generated and committed;
  `types.ts` becomes the stable hand-written surface: re-exports/
  aliases from the generated file, keeps `JobStatusColor` and the 8
  contract-less types (each marked `// no backend response_model yet —
  hand-maintained, see A5.2`). Two files, not a "manual region inside
  the generated file" (generators do not guarantee header preservation).
- CI wiring (no job has both toolchains today): backend job exports
  `openapi.json` as a workflow artifact; frontend job (already has
  node) downloads it, runs `openapi-typescript`, and diffs against the
  committed `types.generated.ts` — diff must be empty. Zero new jobs.
- Ratchet: a backend test enumerates endpoints lacking `response_model`,
  asserts the set equals the known-current list (explore + query
  endpoints). New endpoints must declare response models; shrinking the
  list means debt paid down.

### A6. Real-corpus fixtures converge
Single helper module owns the `app`/`client` fixtures for the three
real-corpus slow tests; the one cross-module `noqa` import
(`test_real_corpus_explore.py:24`) and the duplicated fixture bodies
(jobs vs query) both disappear. Guard WITHOUT subprocess-pytest (a
pytest-inside-pytest that risks pulling testcontainers into the fast
path): one fast test imports the three real-corpus modules and asserts
(a) each module's `pytestmark` composes the slow + key-gate marks, and
(b) the fixtures resolve from the shared helper (source inspection or
`pytest.PyCollector` metadata), so a deleted fixture import fails loudly
instead of silently skipping.

### A7. Small convergences
- `QueryError`/`ExploreReadError`: shared base class, shared
  "查詢中斷" constant; route translation unchanged.
- Frontend: `bodyOf(error) -> Promise<Record<string, unknown> & {detail?:
  string}>` as the single core in `api/client.ts`; `detailOf(error) ->
  Promise<string>` wraps it. The 5 string-returning call sites keep
  `detailOf`; `SettingsPanel.tsx:24` switches to `bodyOf`. Two functions
  by design — the 6 copies are NOT identical (SettingsPanel needs the
  whole body).

### Verification (Wave A)
- Backend: `uv run pytest -q -m "not slow"` green including new tests
  (audit rows, service ownership, ordering, drift gates, fixture guard).
  Note: "fast" here still means Docker testcontainers, as today.
- Frontend: `npx vitest run`, `npx tsc -b --noEmit`, `npm run build`.
- CI green on branch (now three checks: backend, frontend+codegen-diff,
  helm); whole-wave scoped review before merge.

## 5. Wave B — docs + comment sweep (`feature/hygiene-docs`)

No behavior changes; the sweep diff must be comments-only (plus the new
guard) so review is trivial.
Plan file: `docs/superpowers/plans/2026-08-23-hygiene-docs.md`.

### B1. Root `README.md` (English, authoritative)
- What/why (team web console for Microsoft GraphRAG), architecture
  sketch (FastAPI + React SPA + Postgres + graphrag CLI in adapters).
- **15-minute quickstart**, written from steps already executed and
  verified in this project's smoke passes (not invented):
  1. Required env vars for compose (the three `:?`-enforced:
     `JWT_SECRET`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`)
     plus pointer to `.env.example` for all 15.
  2. `docker compose up --build -d`; UI on :8080.
  3. Bootstrap admin login → forced password change on first login.
  4. Create project (note `input_file_type` is fixed at creation).
  5. Upload corpus files.
  6. Project Settings → Env: set `GRAPHRAG_API_KEY` (per-project,
     masked read-back).
  7. First index job; note tiny corpora may need the full method
     (spec §13 finding).
  8. Query (4 modes, SSE) and Explore (tables + graph).
- Known caveats surfaced for normal readers (currently spec-only):
  graphrag 3.1.0 pin reason (no lancedb wheel on mac x86), macOS
  keychain note for `docker compose build`.
- Links: `.env.example`, `CONTRIBUTING.md`, helm chart, zh-TW mirror.

### B2. `docs/zh-TW/README.md` — mirror of B1
zh-TW translation of the README/quickstart only. **Document language
policy (made explicit, acknowledging current reality):** existing
specs/plans ≤ 2026-08-23 are historical records in mixed language
(the 2026-08-19 design spec is zh-TW) — they stay as-is, untranslated,
and remain authoritative for the decisions they record; all NEW
documents are English, with `docs/zh-TW/` reserved for operator-facing
mirrors (currently README only). Not "everything becomes English" — the
stock is what it is; the rule governs from now on.

### B3. One-time comment sweep
All zh-TW code comments → English. Corrected coverage list (measured
~230 lines): backend src + `migrations/env.py` (114 lines / 22 files),
frontend `src` (~45), deploy/helm values + templates `_helpers.tpl`
(`{{/* */}}` syntax), `docker-compose.yml`, `.env.example`,
`backend/Dockerfile`, `backend/pyproject.toml`, `frontend/nginx.conf`,
`frontend/src/index.css` (`/* */`), `.github/workflows/ci.yml`. Rules:
UI strings, API error-detail strings, operator-facing output (helm
`NOTES.txt` — decided: it is output text shown to operators at install
time, i.e. zh-TW by design, NOT a comment), and test assertions quoting
zh-TW UI text are untouched. Sweep commits are mechanical, one per area
(backend / frontend / deploy+config).

### B4. CI comment guard
One scanner, comment-syntax-aware per file type, run as a fast backend
test + called directly in CI:
- `.py` — tokenize COMMENT tokens + docstrings.
- `.ts/.tsx` — `//` and `/* */` content.
- `.yml/.yaml`, `Dockerfile`, `.toml`, `.conf`, `.env.example` — `#`.
- `.css` — `/* */`; helm `.tpl` — `{{/* */}}`.
- Scan roots: `backend/src`, `backend/tests`, `backend/migrations`,
  `frontend/src`, `deploy`, `.github/workflows`, `docker-compose.yml`,
  `.env.example`, `backend/Dockerfile`, `backend/pyproject.toml`,
  `frontend/nginx.conf`. Exclusions: `node_modules`, `dist`, vendored
  helm chart tarball, `NOTES.txt` (output, not comment).
- Escape hatch: deliberate CJK in a comment is prefixed `zh-TW:` (e.g.
  `// zh-TW: asserts UI string`); allowlist file for pathological
  cases, expected near-empty. String literals are never flagged (only
  comment syntax is scanned).
- Self-test: adding one zh-TW comment in a fresh temp file must fail
  the scanner; the `zh-TW:` prefix must pass it.

### B5. Policy sync
- `AGENTS.md`: comments policy becomes "English only (CI-enforced)";
  the stale sentence "API responses follow the backend schemas in
  `api/schemas.py`" is corrected to reality (models live in
  `api/schemas.py` AND per-route modules; the generated OpenAPI
  document is the contract surface — see A5.2); test counts refreshed
  after Wave A (coupling: A5/A6 change test counts — B merges after A
  so the numbers are final).
- `CONTRIBUTING.md`: `docs/zh-TW/` exists and mirrors the README —
  mirror-maintenance rule (README change ⇒ mirror update in same PR);
  the B2 document-language policy (historical specs stay, new docs
  English) replaces "may be added later".
- `frontend/README.md`: Vite boilerplate replaced by a pointer to the
  root README.

### Verification (Wave B)
- Guard green on swept tree; self-test proves it catches violations.
- README quickstart steps cross-checked against the verified smoke-pass
  steps; `docker compose config`, `helm lint` + `helm template` still
  pass if their comment files were touched.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A1/A2 refactor breaks audit semantics | audit-row tests pin contract before moving code |
| A1 ordering change introduces partial-write window | tmp + atomic rename; residual crash window documented as accepted |
| A4 to_thread widens upload-quota TOCTOU window | accepted: probability change, not correctness regression; TOCTOU itself deferred (§3) and now flagged with this interaction |
| A5.1 compare_metadata false positives | fixed category filter (fail/ignore lists in A5.1); ignore-list changes require spec edit |
| A5.2 codegen churns on every run | pin `openapi-typescript` version; diff-only in CI; generated file never hand-edited |
| A5.2 untyped endpoints stay untyped | ratchet pins the known list; new endpoints must declare response_model |
| B3 sweep accidentally edits a string | comments-only diff enforced by review; UI-string tests (57) unchanged and green |
| Guard false positives (Dockerfile/toml/tpl syntax quirks) | `zh-TW:` escape + allowlist; scanner stays dumb and local |
| Two waves drift if A slips | B's only hard dependency on A: test-count refresh in B5; merge order preserved |

## 7. Order

Wave A first (behavior changes, clean review), Wave B second
(mechanical + docs, isolated diff). Plan files, named now:
`docs/superpowers/plans/2026-08-23-hygiene-code.md` (Wave A) and
`docs/superpowers/plans/2026-08-23-hygiene-docs.md` (Wave B). Each wave:
plan → implement → review → CI green → PR rebase-merge → main CI green.
