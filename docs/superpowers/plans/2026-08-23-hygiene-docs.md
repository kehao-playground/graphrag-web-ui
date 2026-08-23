# Hygiene Remediation Wave B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Root README with a verified 15-minute quickstart (+ zh-TW mirror), one-time sweep of ~230 zh-TW comment lines to English, and a CI-enforced comment-language guard — per `docs/superpowers/specs/2026-08-23-hygiene-remediation-design.md` §5.

**Architecture:** Five tasks on branch `feature/hygiene-docs` (branched from main AFTER Wave A merges — B5's test counts depend on A). B1/B2 author the README pair; B3 sweeps comments in three mechanical area commits; B4 lands a comment-language scanner as a fast backend test (CI rides the existing pytest step — the spec's optional standalone script is dropped as a simplification, the test is the enforcement); B5 syncs policy docs. No behavior changes anywhere: the sweep diff must be comments-only.

**Tech Stack:** Markdown, pytest (stdlib-only scanner), GitHub Actions (unchanged jobs).

## Global Constraints

- Comments/docstrings English-only after this wave; UI strings, API error-detail strings, and operator-facing output (helm `NOTES.txt`) remain zh-TW BY DESIGN and are never touched.
- The sweep commits are comments-only — a reviewer must be able to verify no code/string changes by diff inspection. Test files asserting zh-TW UI strings keep those string literals; only their comments translate.
- `docs/superpowers/` specs and plans are historical records (mixed language) — NEVER translated, never in scanner scope.
- README quickstart steps must come from verified facts only (this project's smoke passes; `.env.example`; spec §8/§13). No invented commands.
- Conventional Commits; each sweep area is its own commit; fast suites green before every commit (`cd backend && uv run pytest -q -m "not slow"` where backend files changed; `cd frontend && npx vitest run && npx tsc -b --noEmit` where frontend files changed).
- After B3's sweep, the tree must already satisfy B4's scanner except for deliberate `zh-TW:`-prefixed escapes and allowlist entries.

---

### Task 1: Root `README.md` — English, authoritative (spec B1)

**Files:**
- Create: `README.md` (repo root)
- Reference material (read, do not modify): `.env.example`, `docker-compose.yml`, `docs/superpowers/specs/2026-08-19-graphrag-web-ui-design.md` §8/§13, `deploy/helm/graphrag-ui/README.md` if present

**Interfaces:**
- Produces: the section skeleton below, filled with verified facts. `docs/zh-TW/README.md` (Task 2) mirrors it 1:1; B5's policy docs link to it.

- [ ] **Step 1: Write the README against this skeleton**

Sections (exact order):

1. **Title + one-paragraph pitch** — team web console for Microsoft GraphRAG: manage workspaces/projects, upload corpora, run indexing jobs, query (local/global/drift/basic, SSE streaming with citations), browse parquet artifacts and a WebGL knowledge graph. Replaces CLI operations for non-technical teammates.
2. **Architecture sketch** (short bullet list, no diagram tooling): React 19 SPA (antd, zh-TW UI) ← nginx; FastAPI backend (domain/services/adapters/api layering; graphrag touched ONLY via subprocess CLI in adapters); Postgres 16; one `graphrag init` workspace per project under `WORKSPACES_DIR`.
3. **Quickstart (15 minutes)** — the numbered happy path:
   1. Prereqs: Docker + Docker Compose; (for local dev only: Python 3.12 + uv, Node 20+).
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
5. **Local development** — backend: `cd backend && uv sync && uv run pytest -m "not slow"` (Docker required; testcontainers); frontend: `cd frontend && npm ci && npm test`. Dev proxy: `API_PROXY_TARGET` (vite).
6. **Deployment** — pointer to `deploy/helm/graphrag-ui` (values.yaml documents every env var; NOTES.txt prints install-time quickstart in zh-TW) and `docker-compose.yml`.
7. **Contributing / docs** — links: `CONTRIBUTING.md`, zh-TW mirror `docs/zh-TW/README.md`, design spec `docs/superpowers/specs/`.

Every command above must be verified against the repo before writing (open `.env.example` for the exact var names/defaults; do not transcribe from memory).

- [ ] **Step 2: Cross-check facts**

Run: `docker compose config` (compose still valid); read back each env var name against `.env.example`; confirm port 8080 against `docker-compose.yml`. No CI for README — accuracy is the test.

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
- Consumes: Task 1's `README.md` (source of truth; structure mirrored 1:1, same section order and commands).
- Produces: the zh-TW operator mirror; B5 records the maintenance rule (README change ⇒ mirror update in the same PR).

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

### Task 3: One-time comment sweep (spec B3) — three area commits

**Files (sweep targets, measured 2026-08-23):**
- Backend src+migrations (~65 lines / 13 files): `adapters/workspace.py`, `adapters/db.py`, `adapters/models.py`, `api/deps.py`, `api/auth_routes.py`, `api/users_routes.py`, `api/health_routes.py`, `api/projects_routes.py`, `api/schemas.py`, `main.py`, `services/users.py`, `services/projects.py`, `services/auth.py`, `services/audit.py`, `services/query.py`, `migrations/env.py` (find others by scanning)
- Backend tests (~49 lines / 9 files): `conftest.py:45-50` clean_db docstring, `test_retention`, `test_dry_run`, `test_explore_api`, `test_job_logs_sse`, `test_jobs_api`, `test_env`, `test_settings`, `test_files`
- Frontend (~45 lines): `stores/auth.ts`, `pages/AdminUsers.tsx`, `pages/ProjectDetail.tsx`, `pages/Projects.tsx`, `pages/Login.tsx`, `components/ExplorePanel.tsx`, `components/JobsPanel.tsx`, `components/Layout.tsx`, `api/types.ts`, `index.css`, `setupTests.ts`
- Deploy + config (~71 lines): `deploy/helm/graphrag-ui/values.yaml`, templates `_helpers.tpl`/`api-deployment.yaml`/`api-service.yaml`/`api-service-alias.yaml`/`ingress.yaml`/`pvc.yaml`/`secret.yaml`, `docker-compose.yml`, `.env.example`, `backend/Dockerfile`, `backend/pyproject.toml` (description string → `"GraphRAG Web UI backend (FastAPI)"` — a STRING, guard won't catch regressions here; review-enforced), `frontend/nginx.conf`, `frontend/src/index.css`, `.github/workflows/ci.yml`

**Rules:** translate comments/docstrings to faithful, concise English (technical meaning preserved, not machine-literal); do NOT touch zh-TW string literals (UI text, API details, pytest marks), `docs/superpowers/**`, or helm `NOTES.txt`; where a comment exists to explain a zh-TW string (e.g. test comments quoting UI text), keep the quoted zh-TW inside the now-English comment — that case uses the `zh-TW:` escape prefix from Task 4 only if the quoted text would otherwise trip the scanner as comment content.

- [ ] **Step 1: Sweep backend (src + migrations + tests)**

Work file by file; after each file, run `cd backend && uv run pytest -q -m "not slow"` once for the whole area at the end. If a translated docstring is asserted anywhere (grep the old text first), keep the assertion in sync.
Commit: `docs(comments): sweep backend zh-TW comments to English`.

- [ ] **Step 2: Sweep frontend**

Same rules; suite `npx vitest run && npx tsc -b --noEmit`.
Commit: `docs(comments): sweep frontend zh-TW comments to English`.

- [ ] **Step 3: Sweep deploy + config**

`docker compose config`, `helm lint deploy/helm/graphrag-ui`, `helm template deploy/helm/graphrag-ui > /dev/null` all still pass.
Commit: `docs(comments): sweep deploy/compose/helm zh-TW comments to English`.

---

### Task 4: CI comment-language guard (spec B4)

**Files:**
- Create: `backend/tests/test_comment_language.py` (scanner + self-tests + repo scan)

**Interfaces:**
- Produces: scanner functions `python_comment_spans(text)`, `line_comment_spans(text, marker)`, `block_comment_spans(text, start, end)`, `tpl_comment_spans(text)`; repo-scan test; escape prefix `zh-TW:`; allowlist `backend/tests/comment_language_allowlist.txt` (one path per line, `#`-comments allowed).
- Scan roots (from spec B4): `backend/src`, `backend/tests`, `backend/migrations`, `frontend/src`, `deploy`, `.github/workflows`, `docker-compose.yml`, `.env.example`, `backend/Dockerfile`, `backend/pyproject.toml`, `frontend/nginx.conf`. Excluded: `node_modules`, `dist`, vendored chart tarballs, `NOTES.txt`, everything under `docs/superpowers/`.

- [ ] **Step 1: Write the scanner + self-tests (failing first against unswept paths — run AFTER Task 3 so only self-tests matter)**

```python
# backend/tests/test_comment_language.py
"""Comment-language guard (spec B4): CJK in code comments fails the build.

Escape hatch: a comment whose first non-space text is `zh-TW:` is
deliberate (e.g. quoting a UI string) and passes. String literals are
never scanned — only comment syntax is. Stdlib only.
"""
import io
import re
import tokenize
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
ESCAPE = re.compile(r"^\s*zh-TW:")


def python_comments(text: str) -> list[str]:
    out = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.COMMENT:
            out.append(tok.string)
    return out  # docstrings: parse via ast in the repo scan below


def marker_comments(text: str, marker: str) -> list[str]:
    return [ln.split(marker, 1)[1] for ln in text.splitlines()
            if marker in ln]


def block_comments(text: str, start: str, end: str) -> list[str]:
    return re.findall(re.escape(start) + r"(.*?)" + re.escape(end),
                      text, flags=re.S)
```

Plus: `ast`-based docstring extraction for `.py`; per-extension dispatch (`.py` → tokenize+ast; `.ts/.tsx` → `//` + `/* */`; `.yml/.yaml/.toml/Dockerfile/.conf` + `.env.example` → `#`; `.css` → `/* */`; `.tpl` → `{{/* */}}`); the repo scan walks the roots above, skips exclusions, and asserts every comment span either matches `ESCAPE` or has no `CJK` match; allowlist paths are skipped with their lines counted. Self-tests: inline sample strings — CJK in `//` fails, same line prefixed `// zh-TW:` passes, CJK inside a string literal passes (never scanned), `.tpl` `{{/* 中文 */}}` fails, allowlisted path passes.

- [ ] **Step 2: Run guard + fix stragglers**

Run: `cd backend && uv run pytest tests/test_comment_language.py -v`
Expected: PASS on the swept tree; any missed comment is either swept (preferred, amend the area commit is NOT allowed — add a `docs(comments): guard stragglers` commit) or deliberately escaped/allowlisted with a reason.

- [ ] **Step 3: Full fast suite + commit**

```bash
cd backend && uv run pytest -q -m "not slow"
git add backend/tests/test_comment_language.py backend/tests/comment_language_allowlist.txt
git commit -m "test(guard): CJK comment scanner with zh-TW escape and allowlist"
```

---

### Task 5: Policy sync (spec B5)

**Files:**
- Modify: `AGENTS.md`, `CONTRIBUTING.md`, `frontend/README.md`

**Interfaces:**
- Consumes: final test counts from merged Wave A (backend fast count, frontend count — read from the freshly run suites, not from this plan).
- Produces: updated language policy everywhere it is stated.

- [ ] **Step 1: AGENTS.md** — comments policy line becomes "Code comments/docstrings: English only — CI-enforced (`backend/tests/test_comment_language.py`); deliberate exceptions escape with a `zh-TW:` prefix"; replace the stale "API responses follow the backend schemas in `api/schemas.py`" sentence with: "API contract surface is the generated OpenAPI document (`openapi.json`, regenerated+diffed in CI); pydantic models live in `api/schemas.py` and per-route modules; frontend types come from `types.generated.ts`"; refresh test counts to Wave A's final numbers.
- [ ] **Step 2: CONTRIBUTING.md** — replace "zh-TW translations may be added under `docs/zh-TW/` later" with the live policy: `docs/zh-TW/` exists, mirrors the README, and a README change updates the mirror in the same PR; historical specs/plans ≤2026-08-23 stay in their original language (records, not living docs); new documents are English.
- [ ] **Step 3: frontend/README.md** — replace Vite boilerplate with a 5-line pointer: what the app is (one line), root README link, dev/test commands, link to `docs/zh-TW/README.md`.
- [ ] **Step 4: Verify + commit** — `docker compose config` untouched-behavior sanity; counts match a fresh `uv run pytest -q -m "not slow"` and `npx vitest run`.
```bash
git add AGENTS.md CONTRIBUTING.md frontend/README.md
git commit -m "docs(policy): English-only comments (CI-enforced), zh-TW mirror rule, OpenAPI contract wording"
```

---

## Final verification (whole wave)

- `cd backend && uv run pytest -q -m "not slow"` (includes the comment guard) && `uv run ruff check`
- `cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build`
- `docker compose config && helm lint deploy/helm/graphrag-ui && helm template deploy/helm/graphrag-ui > /dev/null`
- README fact spot-check: every env var name greps true in `.env.example`; every port matches compose.
- Push `feature/hygiene-docs`, CI green, scoped wave review (comments-only diffs are reviewed by inspection), PR rebase-merge.

## Self-Review (done)

- Spec §5 coverage: B1→Task 1, B2→Task 2, B3→Task 3, B4→Task 4 (script dropped, test-only enforcement — noted in Architecture), B5→Task 5. N6 (tests in sweep list) and N7 (pyproject string handling) folded into Task 3; NOTES.txt exclusion into Task 4 interfaces.
- Type consistency: scanner function names used in Task 4 only; README skeleton referenced by Task 2 verbatim.
- Placeholder scan: README skeleton carries the full verified step list with exact var names; scanner code is concrete; no TBDs.
- Dependency: plan assumes Wave A merged (test counts, openapi wording in B5).
