# Hygiene Remediation Design

Date: 2026-08-23
Status: approved by user (scope + two-wave structure)
Input: four read-only audits (architecture conformance, backend smells,
frontend smells, language/docs inventory), 2026-08-23.

## 1. Problem

The five feature phases are merged and green (backend 205 fast + 5 slow,
frontend 57, CI green), but four audits found:

1. **Clean-architecture spirit debt** — the codebase conforms to the letter
   of every AGENTS.md §9 rule, but:
   - `api/env_routes.py:83,99` and `api/files_routes.py:107,138` call
     `audit()` add + `db.commit()` directly. Services must own the
     transaction boundary; routes must not commit.
   - `api/users_routes.py:32-48,99` runs raw `select()` queries and holds
     the last-active-admin business rule in the handler.
   - `_ws_path` — the workspace path-safety guard — lives in
     `services/projects.py` and is privately imported by 8 services and
     `services/env_file.py`.
   - `query_routes._prepare_query` and `explore_routes._allowed` are
     byte-identical; project-permission scaffolding is copy-pasted 13×
     across 6 route modules.
2. **Robustness smells** — blocking sync I/O (`rglob`, `disk_usage`,
   `stat`) inside async handlers (event-loop freeze risk on large
   workspaces); no schema-drift test between alembic head and
   `Base.metadata`; `frontend/src/api/types.ts` is a hand-maintained
   mirror of `api/schemas.py` with no drift check; real-corpus test
   fixtures duplicated across 3 files via cross-module `noqa` imports
   (the exact pattern that silently broke `query_client` in PR #5);
   `QueryError`/`ExploreReadError` parallel class hierarchies; frontend
   `detailOf` helper copy-pasted 6×.
3. **Language drift** — ~150 zh-TW code-comment/docstring lines across
   30+ files (backend ~60/16 files, frontend ~40, deploy/helm/compose/
   .env.example ~50). Policy says English-authoritative with boy-scout
   migration on touch; migration never catches up with the stock.
4. **Onboarding gap** — no root README (Foundation-B plan recorded
   "README not present — skip"), no quickstart, no runbook. The 15-minute
   happy path (env vars → compose up → bootstrap admin → create project
   → upload → set `GRAPHRAG_API_KEY` → first index → query → explore)
   exists only inside the zh-TW design spec and per-phase plans; a
   normal reader cannot discover it. The UI is zh-TW but no zh-TW
   operator docs exist (`docs/zh-TW/` reserved in CONTRIBUTING.md, never
   created).

## 2. Goals

- Restore clean-architecture *spirit*: transactions owned by services,
  routes translate only, path guard in domain.
- Close the two drift surfaces (DB schema, API types) with generated or
  tested contracts instead of hand-mirroring.
- Remove the known freeze risk and fixture-duplication hazards.
- Give the repo a root README with a verified 15-minute quickstart, and
   a zh-TW mirror of it under `docs/zh-TW/`.
- End the mixed-language comment state: one-time sweep to English plus a
  CI guard so the stock cannot regrow.
- Code comments stay English-only (user decision 2026-08-23: zh-TW
  versions are for *documents*, never comments; UI strings and API error
  details remain zh-TW by design).

## 3. Non-goals (recorded for future feature work)

Explicitly deferred, in writing, so they are not lost:

- Route project-permission scaffolding extraction (13 copies) and
  `_prepare_query`/`_allowed` unification.
- `run_query`/`stream_query` shared prelude extraction.
- Subprocess output streaming (init/dry-run currently buffer full CLI
  output via `capture_output=True`; index failure path reads the whole
  log before slicing the tail).
- Upload quota TOCTOU race (pre-write snapshot vs final size under
  concurrent uploads).
- `services/query.py` split (228 lines, POST + SSE).

## 4. Wave A — code remediation (`feature/hygiene-code`)

TDD throughout; fast suites must stay green; no behavior change is
allowed without a test pinning the new contract first.

### A1. Transaction ownership returns to services
Move the `audit()` add + commit blocks from `env_routes.py` (lines 83,
99) and `files_routes.py` (lines 107, 138) into the corresponding
service functions (`services/env_file.py`, `services/files.py`).
Services commit; routes translate errors. Existing audit-row assertions
must keep passing unchanged — audit content is contract, not
implementation detail.

### A2. `users_routes` stops querying
Move the raw `select()` statements (lines 32-48) and the
last-active-admin rule (line 99) into `services/users.py`. The handler
keeps only: parse → call service → translate domain error to HTTP.

### A3. `_ws_path` moves to domain
New `domain/workspaces.py` (pure path-safety logic, no I/O). All 8
services plus `env_file.py` import from there; `services/projects.py`
drops its local definition outright — all references are internal, so
this is a clean cutover with no re-export shim.

### A4. Async handlers stop blocking
Wrap sync filesystem calls in `asyncio.to_thread`, matching the pattern
already used by explore routes: `services/files.py:80-83` (`rglob`),
`services/jobs.py:35-41,85-108` (preflight `disk_usage`/`_tree_bytes`),
`api/health_routes.py:30-41` (`disk_usage`). No API shape change.

### A5. Two drift gates
1. **Schema drift**: fast test that runs alembic migrations to head on
   the testcontainer Postgres and compares table/column structure
   against `Base.metadata`. Fails when someone edits models without a
   migration (AGENTS.md rule "alembic migrations only" becomes enforced).
2. **API types drift**: `backend/scripts/gen_types.py` generates
   `frontend/src/api/types.generated.ts` from the pydantic schemas;
   frontend imports switch to it; CI step regenerates and diffs —
   committed file must be fresh. Hand-maintained mirror deleted (clean
   cutover). Pydantic-to-TS via a pinned small generator dependency; if
   the generator's output for SSE-stream schemas proves awkward, those
   few shapes may stay hand-written *inside the generated file's header
   region marked as manual*, with the drift test covering the rest.

### A6. Real-corpus fixtures converge
Single helper module (or conftest) owns `app`/`client` fixtures for the
three real-corpus slow tests; cross-module `noqa: F401` imports
disappear. `pytest --setup-plan` with a dummy key must list every
real-corpus test (regression guard for the silent-skip failure mode).

### A7. Small convergences
- `QueryError`/`ExploreReadError`: shared base class, shared
  "查詢中斷" constant; route translation unchanged.
- Frontend: one `detailOf` in `api/client.ts`; six copies collapse.
  (zh-TW detail strings still surfaced verbatim — UI contract.)

### Verification (Wave A)
- Backend: `uv run pytest -q -m "not slow"` green including new tests
  (audit-rows, service ownership, drift tests, fixture plan guard).
- Frontend: `npx vitest run`, `npx tsc -b --noEmit`, `npm run build`.
- CI green on branch; whole-wave scoped review before merge (reviewer
  substitute: generic task agent, opus reviewer quota permitting).

## 5. Wave B — docs + comment sweep (`feature/hygiene-docs`)

No behavior changes; the sweep diff must be comments-only (plus the new
guard test) so review is trivial.

### B1. Root `README.md` (English, authoritative)
- What/why (team web console for Microsoft GraphRAG), architecture
  sketch (FastAPI + React SPA + Postgres + graphrag CLI in adapters).
- **15-minute quickstart**, written from the steps already executed and
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
zh-TW translation of the README/quickstart only. Development-facing
docs (CONTRIBUTING, AGENTS, plans, specs) stay English-only.

### B3. One-time comment sweep
All zh-TW code comments/docstrings → English across backend, frontend,
deploy/helm, docker-compose, `.env.example`, `migrations/env.py`
(~150 lines, 30+ files). Rules: UI strings, API error-detail strings,
and test assertions quoting zh-TW UI text are untouched. Sweep commits
are mechanical, one commit per area (backend / frontend / deploy).

### B4. CI comment guard
- Python: tokenize-based scan of `.py` files — COMMENT tokens and
  docstrings containing CJK fail the build.
- TS/TSX/YAML: regex scan of `//` and `/* */` / `#` comment content for
  CJK; deliberate exceptions escape with a `zh-TW:` prefix
  (e.g. `// zh-TW: asserts UI string`). String literals are not
  comments and never flagged.
- Shipped as a fast backend test + a tiny script CI calls; allowlist
  file for pathological cases, expected to stay near-empty.

### B5. Policy sync
- `AGENTS.md`: comments policy becomes "English only (CI-enforced)";
  test counts refreshed after Wave A.
- `CONTRIBUTING.md`: `docs/zh-TW/` is no longer "may be added later" —
  it exists and mirrors the README; state the mirror-maintenance rule
  (README change ⇒ mirror update in same PR).
- `frontend/README.md`: Vite boilerplate replaced by a pointer to the
  root README.

### Verification (Wave B)
- Guard test green on swept tree; deliberately re-adding one zh-TW
  comment makes it fail (self-test).
- README quickstart steps cross-checked against the verified smoke-pass
  steps; `docker compose config`, `helm lint` + `helm template` still
  pass if their comment files were touched.

## 6. Risks

| Risk | Mitigation |
|---|---|
| A1/A2 refactor breaks audit semantics | audit-row tests pin contract before moving code |
| A5 codegen churns types on every run | pin generator version; output diff-only in CI; manual region only if SSE shapes force it |
| B3 sweep accidentally edits a string | comments-only diff enforced by review; UI-string tests (57) unchanged and green |
| Comment guard false positives | `zh-TW:` escape prefix + allowlist; keep scanner dumb and local |
| Two waves drift if A slips | B independent of A except README env-var table; merge order preserved |

## 7. Order

Wave A first (behavior changes, clean review), Wave B second
(mechanical + docs, isolated diff). Each wave: plan → implement →
review → CI green → PR rebase-merge → main CI green.
