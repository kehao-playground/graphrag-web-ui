# Hygiene Remediation Wave B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Root README with a verified 15-minute quickstart (+ zh-TW mirror), a scanner-guided sweep of ~230 zh-TW comment lines to English, and a CI-enforced comment-language guard — per `docs/superpowers/specs/2026-08-23-hygiene-remediation-design.md` §5, revised after user plan-review (2026-08-23: scanner-first ordering, generated-chain handling, README fact fixes).

**Architecture:** Five tasks on branch `feature/hygiene-docs` (cut from main@8a081a1 — Wave A fully merged; B5's counts come from a fresh run after Task 3 adds its tests). T1/T2 author the README pair; **T3 writes the scanner FIRST (self-tests + list mode)**; T4 sweeps the tree guided by the scanner's violation output (area commits; the schemas.py→openapi.json→types.generated.ts generated chain is its own commit); T5 syncs policy docs. No behavior changes: every sweep commit is comments-only except the generated-chain commit, which pairs source translation with regeneration.

**Tech Stack:** Markdown, pytest (stdlib-only scanner), openapi regen (`backend/scripts/gen_openapi.py`, `frontend npm run gen:types`), GitHub Actions (unchanged jobs — the guard rides the existing pytest step; the spec's optional standalone script stays dropped).

## Global Constraints

- Comments/docstrings English-only after this wave; UI strings, API error-detail strings, and operator-facing output (helm `NOTES.txt`) remain zh-TW BY DESIGN and are never touched.
- **Generated-artifact chain (hard rule):** `backend/src/graphrag_ui/api/schemas.py` docstrings flow into `openapi.json` (`\u`-escaped — invisible to plain CJK grep) and from there into `frontend/src/api/types.generated.ts` JSDoc. Generated files are NEVER hand-edited (CI regen+diff gates enforce). Any commit that translates a schemas.py docstring MUST, in the same commit, run `cd backend && uv run python scripts/gen_openapi.py` and `cd frontend && npm run gen:types` and commit the regenerated files. Both generated files are excluded from the scanner.
- The authoritative to-sweep list is the scanner's violation output (Task 3's list mode), NOT any hand-written file list; the distributions below are 2026-08-23 measurements for estimation only.
- Sweep commits are comments-only by diff inspection (exception: the generated-chain commit above; and string-literal translations explicitly listed in Task 4). Test files asserting zh-TW UI strings keep those literals; only their comments translate.
- `docs/superpowers/` specs and plans are historical records (mixed language) — NEVER translated, never in scanner scope.
- README quickstart steps come from verified facts only. Verified corrections (2026-08-23 review): search runs **in-process** `graphrag.api` inside the shielded adapter; indexing runs via subprocess CLI — both graphrag touchpoints live only in `adapters/`. Frontend requires **Node 24** (jsdom/undici need ≥22; CI pins 24).
- Conventional Commits; fast suites green before every commit (`cd backend && uv run pytest -q -m "not slow"` where backend files changed; `cd frontend && npx vitest run && npx tsc -b --noEmit` where frontend files changed). After Task 4, the tree must satisfy the scanner except deliberate `zh-TW:` escapes and allowlist entries.

---

### Task 1: Root `README.md` — English, authoritative (spec B1)

**Files:**
- Create: `README.md` (repo root)
- Reference material (read, do not modify): `.env.example`, `docker-compose.yml`, `docs/superpowers/specs/2026-08-19-graphrag-web-ui-design.md` §8/§13

**Interfaces:**
- Produces: the section skeleton below with verified facts. `docs/zh-TW/README.md` (Task 2) mirrors it 1:1; T5's policy docs link to it.

- [ ] **Step 1: Write the README against this skeleton**

Sections (exact order):

1. **Title + one-paragraph pitch** — team web console for Microsoft GraphRAG: manage workspaces/projects, upload corpora, run indexing jobs, query (local/global/drift/basic, SSE streaming with citations), browse parquet artifacts and a WebGL knowledge graph. Replaces CLI operations for non-technical teammates.
2. **Architecture sketch** (short bullets, no diagram tooling): React 19 SPA (antd, zh-TW UI) ← nginx; FastAPI backend (domain/services/adapters/api layering); **two graphrag touchpoints, both confined to `adapters/`: indexing runs the graphrag CLI as a subprocess; query/search calls `graphrag.api` in-process inside a shielded module (`adapters/graphrag_search.py` — shielded because graphrag's dependency chain loads `.env`/dotenv into `os.environ` on import)**; Postgres 16; one `graphrag init` workspace per project under `WORKSPACES_DIR`.
3. **Quickstart (15 minutes)** — the numbered happy path:
   1. Prereqs: Docker + Docker Compose; Node **24** and Python 3.12 + uv only for local development (jsdom/undici need Node ≥22; CI pins 24).
   2. `cp .env.example .env`, then set the three compose-enforced vars: `JWT_SECRET` (any ≥32-char secret), `BOOTSTRAP_ADMIN_EMAIL` (NOT a `.local` domain — login rejects special-use domains), `BOOTSTRAP_ADMIN_PASSWORD`. All 15 variables and defaults: see `.env.example`.
   3. `docker compose up --build -d` → UI at `http://localhost:8080` (postgres + alembic migrations start automatically; API waits for PG health).
   4. Log in as the bootstrap admin → the UI forces a password change on first login.
   5. Create a project: pick `input_file_type` (text/csv/json) — it is FIXED at creation.
   6. Upload corpus files (per-file cap `UPLOAD_MAX_FILE_MB`, project quota `PROJECT_QUOTA_MB`).
   7. Project Settings → Env: set `GRAPHRAG_API_KEY` (per-project, stored in the workspace `.env`, read back masked). Without it, indexing jobs fail.
   8. Jobs → run index (method `fast` or `standard`). Caveat from real-corpus testing: on tiny corpora the `fast` method can fail (spec §13) — use `standard` for the first run on small test corpora. Follow job progress via the live log viewer.
   9. Query: all four modes stream over SSE with inline citations.
   10. Explore: artifact tables (entities/relationships/communities/documents/community_reports/text_units) + 圖譜 WebGL graph view.
4. **Known caveats** — graphrag pinned `==3.1.0` (lancedb has no macOS x86 wheel; see spec §13); on macOS `docker compose build web` may hang on keychain prompts — run `docker compose build api` + serve frontend via `npm run preview` for UI work, or unlock the keychain first.
5. **Local development** — backend: `cd backend && uv sync && uv run pytest -m "not slow"` (Docker required; testcontainers); frontend: `cd frontend && npm ci && npm test` (Node 24). Dev proxy: `API_PROXY_TARGET` (vite).
6. **Deployment** — pointer to `deploy/helm/graphrag-ui` (values.yaml documents every env var; NOTES.txt prints install-time quickstart in zh-TW) and `docker-compose.yml`.
7. **Contributing / docs** — links: `CONTRIBUTING.md`, zh-TW mirror `docs/zh-TW/README.md`, design spec `docs/superpowers/specs/`.

Every command and claim must be re-verified against the repo before writing (open `.env.example`/`docker-compose.yml`/`api/query_routes.py` for the mode Literal; do not transcribe from memory).

- [ ] **Step 2: Cross-check facts**

Run: `docker compose config` (compose still valid); each env var name against `.env.example`; port 8080 against `docker-compose.yml`; query modes against `api/query_routes.py:22`; Node version against `.github/workflows/ci.yml`. No CI for README — accuracy is the test.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: root README with verified 15-minute quickstart"
```

---

### Task 2: `docs/zh-TW/README.md` — mirror (spec B2)

**Files:**
- Create: `docs/zh-TW/README.md`

**Interfaces:**
- Consumes: Task 1's `README.md` (source of truth; structure mirrored 1:1, same section order and commands — including the corrected two-touchpoint architecture wording and Node 24).
- Produces: the zh-TW operator mirror; T5 records the maintenance rule (README change ⇒ mirror update in the same PR).

- [ ] **Step 1: Translate**

Mirror every section; commands, env var names, ports, and code spans stay verbatim English; prose is 正體中文 (Taiwan conventions: 設定 not 设置, 資料庫, 佇列). UI term glossary for consistency with the app: 專案/成員/檔案/設定/環境/工作/查詢/探索/圖譜/資料表.

- [ ] **Step 2: Structural parity check**

Headings count and order identical; every code block present; links resolve (relative paths from `docs/zh-TW/` need `../..` prefixes).

- [ ] **Step 3: Commit**

```bash
git add docs/zh-TW/README.md
git commit -m "docs(zh-TW): README operator mirror"
```

---

### Task 3: Comment-language scanner — written FIRST, list mode drives the sweep (spec B4)

**Files:**
- Create: `backend/tests/test_comment_language.py` (scanner module: functions + self-tests + `python -m` list mode)

**Interfaces (unified names — the code defines them):**
- Produces: `python_comments(text) -> list[tuple[int, str]]`, `python_docstrings(text) -> list[tuple[int, str]]`, `marker_comments(text, marker) -> list[tuple[int, str]]`, `block_comments(text, start, end) -> list[tuple[int, str]]` — every function returns `(lineno, comment_text)` pairs so `--list` can print `path:lineno: comment`; extension dispatch (`.py` → tokenize COMMENT + ast docstrings; `.ts/.tsx` → `//` + `/* */`; `.yml/.yaml/.toml/Dockerfile/.conf/.ini` and `.env.example` → `#` only — INI also allows `;` comments but alembic.ini uses none today, kept out to stay dumb; `.css` → `/* */`; `.html` → `block_comments` with `<!--` … `-->`; `.tpl` → `block_comments` with `{{/*` … `*/}}`); `CJK` regex including CJK punctuation and fullwidth forms: `[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff01-\uff60]` (§ is U+00A7, outside the classes — English comments keep it)
- Scan roots: `backend/src`, `backend/tests`, `backend/migrations`, `backend/scripts`, `backend/alembic.ini`, `backend/Dockerfile`, `backend/pyproject.toml`, `frontend/src`, `frontend/vite.config.ts`, `frontend/Dockerfile`, `frontend/index.html`, `frontend/nginx.conf`, `deploy`, `.github/workflows`, `docker-compose.yml`, `.env.example`. Exclusions: only whitelisted extensions are scanned (so vendored chart tarballs and binaries are naturally skipped); plus explicit path exclusions `frontend/src/api/types.generated.ts`, `openapi.json` (generated artifacts — see Global Constraints), and `docs/superpowers/`
- **Repo-scan gating test is NOT added in this task** (it would be red until Task 4 sweeps). This task lands: scanner functions, self-tests, list mode, allowlist file.

- [ ] **Step 1: Write self-tests first (failing)**

Cases (samples MUST be assigned variables, never function-first triple-quoted strings — the ast docstring extractor would eat them): CJK in `//` fails; same line prefixed `// zh-TW:` passes; CJK inside a string literal passes (never scanned); `.tpl` `{{/* 中文 */}}` fails (cross-line case included, `re.S`); fullwidth comma `，` and 、。「」 each fail; `§` in an English comment passes; allowlisted path passes; missing allowlist file tolerated; `.py` docstring CJK fails; `#` comment in yaml/toml-style text fails. TDD: red → green.

- [ ] **Step 2: Implement the scanner (stdlib only)**

```python
# backend/tests/test_comment_language.py
"""Comment-language scanner (spec B4): CJK in code comments fails the build.

Escape hatch: a comment whose first non-space text is `zh-TW:` is
deliberate (e.g. quoting a UI string) and passes. String literals are
never scanned — only comment syntax is. Known dumb-scanner tradeoff: a
`//` inside a string literal followed by CJK would false-positive; the
repo has no such case today and the zh-TW: escape covers it.
Stdlib only. `python backend/tests/test_comment_language.py --list`
prints violations (file:line: comment) — the authoritative sweep list.
"""
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CJK = re.compile(r"[\u3000-\u303f\u3400-\u4dbf\u4e00-\u9fff\uff01-\uff60]")
ESCAPE = re.compile(r"^\s*zh-TW:")


def python_comments(text: str) -> list[tuple[int, str]]:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            out.append((tok.start[0], tok.string))
    return out


def python_docstrings(text: str) -> list[tuple[int, str]]:
    out = []
    for n in ast.walk(ast.parse(text)):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef,
                          ast.AsyncFunctionDef)):
            doc = ast.get_docstring(n)
            if doc:
                out.append((n.body[0].lineno, doc))
    return out


def marker_comments(text: str, marker: str) -> list[tuple[int, str]]:
    return [(i, ln.split(marker, 1)[1])
            for i, ln in enumerate(text.splitlines(), 1) if marker in ln]


def block_comments(text: str, start: str, end: str) -> list[tuple[int, str]]:
    pat = re.compile(re.escape(start) + r"(.*?)" + re.escape(end), re.S)
    return [(text[:m.start()].count("\n") + 1, m.group(1))
            for m in pat.finditer(text)]
```

Plus: the extension dispatch table, root/exclusion walk, allowlist reader, self-tests, and a `--list` entry point printing `path:lineno` for every violation (drive the sweep from it).

- [ ] **Step 3: Run list mode; save the output**

Run: `mkdir -p .superpowers/sdd/2026-08-23-hygiene-docs && cd backend && uv run python tests/test_comment_language.py --list | tee ../.superpowers/sdd/2026-08-23-hygiene-docs/sweep-list.txt`
Expected: ~230 zh-TW comment LINES ≈ 180–210 VIOLATIONS (list mode counts one multi-line docstring as a single entry — a lower number does not mean the scanner is broken). This file is Task 4's work order (per-area partitioning by path prefix).

- [ ] **Step 4: Create allowlist, then verify, then commit**

```bash
printf '# Deliberate CJK-comment exceptions, one path per line, reason as # comment\n' > backend/tests/comment_language_allowlist.txt
cd backend && uv run pytest tests/test_comment_language.py -v && uv run pytest -q -m "not slow"
git add backend/tests/test_comment_language.py backend/tests/comment_language_allowlist.txt
git commit -m "test(guard): CJK comment scanner with list mode, zh-TW escape, allowlist"
```

---

### Task 4: Scanner-guided sweep (spec B3) — area commits, generated chain included

**Files:**
- Work order: `.superpowers/sdd/2026-08-23-hygiene-docs/sweep-list.txt` (Task 3's scanner output — authoritative; the 2026-08-23 measurement for estimation: backend src+migrations ~65 lines / ~17 files — includes `api/explore_routes.py`, `api/query_routes.py`, `api/jobs_routes.py`, `adapters/frame_cache.py`, `adapters/index_runner.py`, `services/errors.py` neighbors, `services/rate_limit.py`, `domain/jobs.py`, `api/schemas.py`; backend tests ~49 lines — real hits live in `test_auth.py`, `test_users.py`, `test_permissions.py`, `test_projects.py`, `test_query_api.py`, `test_query_stream_sse.py`, `test_frame_cache.py`, `test_health.py`, `test_real_corpus_query.py`, `conftest.py`; frontend ~43 lines — components incl. `JobLogViewer.tsx`, `GraphView.tsx`, `api/client.ts`, and `__tests__/` files; deploy+config ~59 lines incl. `Chart.yaml`; `frontend/src/index.css` belongs to the FRONTEND commit only)
- String-literal translations explicitly in scope: `backend/pyproject.toml:4` description → `"GraphRAG Web UI backend (FastAPI)"` (a STRING — the guard cannot catch regressions here; review-enforced)

**Rules:** translate comments/docstrings to faithful, concise English (technical meaning preserved, not machine-literal); full-width punctuation inside translated comments becomes ASCII (the widened CJK class would otherwise flag it); do NOT touch zh-TW string literals (UI text, API details, pytest marks, `INTERRUPTED_DETAIL`), `docs/superpowers/**`, or helm `NOTES.txt`; a comment quoting zh-TW keeps the quote but uses the `zh-TW:` escape prefix if the quoted text would otherwise trip the scanner.

- [ ] **Step 1: Sweep backend src+migrations per sweep-list; regen gates**

Every file on the list under `backend/src` + `backend/migrations`. If ANY `api/schemas.py` docstring changed: same commit runs `uv run python scripts/gen_openapi.py` (updates `openapi.json`) — and `cd frontend && npm run gen:types` + commit `types.generated.ts` in the SAME commit (the generated chain — see Global Constraints). Prefer a dedicated commit for the chain: translate `schemas.py` docstrings + both regenerated artifacts together, message `docs(comments): translate schemas.py docstrings; regenerate openapi.json and types.generated.ts`.
Verify: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`; regen diffs empty after commit.
Backend tests area: separate commit `docs(comments): sweep backend tests zh-TW comments to English`.

- [ ] **Step 2: Sweep frontend per sweep-list (incl. index.css; NOT types.generated.ts)**

Verify: `cd frontend && npx vitest run && npx tsc -b --noEmit`. If a translated docstring is asserted anywhere (grep the old text first), keep the assertion in sync.
Commit: `docs(comments): sweep frontend zh-TW comments to English`.

- [ ] **Step 3: Sweep deploy+config per sweep-list (Chart.yaml, values, templates, compose, .env.example, Dockerfiles, ci.yml, nginx.conf, alembic.ini if listed)**

Verify: `docker compose config`, `helm lint deploy/helm/graphrag-ui`, `helm template deploy/helm/graphrag-ui > /dev/null`.
Commit: `docs(comments): sweep deploy/compose/helm zh-TW comments to English`.

- [ ] **Step 4: Re-run list mode — must be empty (minus escapes/allowlist); land the gating test**

Add the repo-scan test to `test_comment_language.py` (walks roots, asserts zero violations outside `zh-TW:` escapes + allowlist). Stragglers found: sweep them in a NEW commit (`docs(comments): guard stragglers`) — never amend an existing area commit.
Run: `cd backend && uv run pytest tests/test_comment_language.py -v` → all green.
Commit: `test(guard): repo-scan gate on swept tree`.

---

### Task 5: Policy sync (spec B5)

**Files:**
- Modify: `AGENTS.md`, `CONTRIBUTING.md`, `frontend/README.md`

**Interfaces:**
- Consumes: test counts from a FRESH run AFTER Task 3's tests exist (do NOT copy Wave A's numbers — AGENTS.md currently reads 210 tests with key (205 fast) / 51→57-era frontend; Task 3 added self-tests to the backend suite). Run the suites now and use those numbers.
- Produces: updated language policy everywhere it is stated.

- [ ] **Step 1: AGENTS.md** — comments policy line becomes "Code comments/docstrings: English only — CI-enforced (`backend/tests/test_comment_language.py`); deliberate exceptions escape with a `zh-TW:` prefix"; replace the stale "API responses follow the backend schemas in `api/schemas.py`" sentence with: "API contract surface is the generated OpenAPI document (`openapi.json`, regenerated+diffed in CI); pydantic models live in `api/schemas.py` and per-route modules; frontend types come from `types.generated.ts` (regenerated via `npm run gen:types`; schemas.py docstring changes flow into it — always regen both)"; refresh test counts from the fresh runs
- [ ] **Step 2: CONTRIBUTING.md** — replace "zh-TW translations may be added under `docs/zh-TW/` later" with the live policy: `docs/zh-TW/` exists, mirrors the README, and a README change updates the mirror in the same PR; historical specs/plans ≤2026-08-23 stay in their original language (records, not living docs); new documents are English.
- [ ] **Step 3: frontend/README.md** — replace Vite boilerplate with a 5-line pointer: what the app is (one line), root README link, dev/test commands (Node 24), link to `docs/zh-TW/README.md`.
- [ ] **Step 4: Verify + commit** — counts match fresh `uv run pytest -q -m "not slow"` and `npx vitest run`.
```bash
git add AGENTS.md CONTRIBUTING.md frontend/README.md
git commit -m "docs(policy): English-only comments (CI-enforced), zh-TW mirror rule, OpenAPI contract wording"
```

---

## Final verification (whole wave)

- `cd backend && uv run pytest -q -m "not slow" && uv run ruff check` (includes the repo-scan gate)
- With `GRAPHRAG_API_KEY` available: one full local suite run (`uv run pytest`) — slow tests assert docstring-adjacent behavior and CI runs them; catch any translated-docstring breakage before push.
- `cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build`
- Regen gates: `cd backend && uv run python scripts/gen_openapi.py && git diff --exit-code ../openapi.json`; `cd frontend && npm run gen:types && git diff --exit-code src/api/types.generated.ts`
- `docker compose config && helm lint deploy/helm/graphrag-ui && helm template deploy/helm/graphrag-ui > /dev/null`
- README fact spot-check: every env var name greps true in `.env.example`; ports/modes/Node version match ci.yml and query_routes.py.
- Push `feature/hygiene-docs`, CI green (backend job now includes the repo-scan gate), scoped wave review (comments-only diffs + the generated-chain commit reviewed as source+artifact pair), PR rebase-merge.

## Self-Review (done)

- User review coverage: blocking 1 (generated chain: Global Constraints hard rule + dedicated chain commit + scanner exclusions + final-verification regen gates) · blocking 2 (scanner-first: Task 3 before Task 4, list mode is the authoritative work order, hand-lists demoted to estimation notes with corrected distributions) · blocking 3 (README: two-touchpoint adapters wording with shield rationale; Node 24 with jsdom/undici rationale) · 4 (unified scanner names; tpl via block_comments) · 5 (CJK classes widened incl. fullwidth/punctuation; § documented outside) · 6 (allowlist created in Task 3 Step 4 + absent-file tolerance) · 7 (B5 counts: fresh-run wording, current AGENTS.md values noted) · 8 (full-suite-with-key verification line) · 9 (index.css frontend-only) · 10 (roots += scripts/vite.config/Dockerfiles/index.html/alembic.ini) · 11 (dumb-scanner `//`-in-string tradeoff documented in module docstring) · 12 (self-test samples as variables rule) · 13 (no-amend sentence reworded) · 14 (whitelisted-extensions-only wording).
- Spec §5 coverage: B1→T1, B2→T2, B4→T3 (scanner), B3→T4 (sweep), B5→T5 — order swapped per review (scanner before sweep), coverage unchanged.
- Placeholder scan: README skeleton carries the corrected verified facts; scanner code concrete; sweep driven by generated list; no TBDs.
- Dependency: plan assumes main@8a081a1 (Wave A merged) — confirmed.

- Review round 2 (2026-08-23): nginx.conf restored to roots; .html dispatch added (block_comments `<!--`…`-->`); all four scanner functions return (lineno, text) so --list prints path:lineno; mkdir -p before tee; "~230 lines" re-anchored as ≈180–210 violations (multi-line docstring = one entry); 400-line-cap exception note dropped from T5 (plan-scoped rule, not repo policy); T3 Step 4 reordered create-allowlist→test→commit; .ini `;` support deliberately skipped (dumb scanner).
