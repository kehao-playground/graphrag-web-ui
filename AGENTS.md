# AGENTS.md

Guidelines for AI coding agents working in this repository.

## Project

GraphRAG Web UI — a team web console for Microsoft GraphRAG. FastAPI backend
manages graphrag workspaces (one project = one `graphrag init` root); React
SPA is the UI. Full design: `docs/superpowers/specs/` (authoritative).
Implementation plans live in `docs/superpowers/plans/` and carry per-task
briefs; their Global Constraints always apply.

## Language & Commits

- Conventional Commits, English subject and body.
- Documentation in English (English stays authoritative); `docs/zh-TW/`
  mirrors the README — README changes update the mirror in the same PR.
- Code comments/docstrings: English only — CI-enforced
  (`backend/tests/test_comment_language.py`); deliberate exceptions
  escape with a `zh-TW:` prefix.
- When editing a file that still has zh-TW comments, migrate the comments
  in the sections you touch. No repo-wide comment rewrites.
- Details and examples: `CONTRIBUTING.md`.

## Architecture Rules (spec §9)

- Layering under `backend/src/graphrag_ui/`:
  - `domain/` — pure logic, no I/O, no external imports
    (no fastapi/sqlalchemy/graphrag).
  - `services/` — use cases; must not import FastAPI or raise
    `HTTPException`; own the transaction boundary (`audit()` adds, services
    commit; `flush → external work → commit` with rollback on failure).
    One exception: the runner-loop writes in `adapters/jobs_repo.py`
    (`claim_next`, `heartbeat`, `set_progress`, `finish`) commit their own
    short transactions; request-path callers never rely on that.
  - `adapters/` — Postgres repos, FS workspace, graphrag integration.
    All graphrag touchpoints live here: subprocess CLI for indexing
    (adapters), and in-process `graphrag.api` imports ONLY via
    `adapters/graphrag_search.py` (env-shielded — litellm runs
    load_dotenv at import time and leaks the nearest .env into
    os.environ); no other graphrag import sites.
  - `api/` — FastAPI routes/schemas/auth; translate service errors to HTTP.
- DB schema changes go through alembic migrations only. Never edit tables
  by hand. `adapters/db.py` engine is lazy — never build engines at module
  import time.
- Environment variable names are fixed: `DATABASE_URL`, `WORKSPACES_DIR`,
  `JWT_SECRET`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`,
  `UPLOAD_MAX_FILE_MB`, `PROJECT_QUOTA_MB`, `MAX_CONCURRENT_JOBS`,
  `JOB_LOG_RETENTION_DAYS`, `JOB_LOG_FAILED_RETENTION_DAYS`,
  `UPDATE_OUTPUT_KEEP_LATEST`, `CACHE_QUOTA_MB`, `DISK_WATERMARK_MB`,
  `QUERY_CACHE_MB`, `QUERY_RATE_LIMIT_PER_HOUR`, `GRAPH_NODE_LIMIT`,
  `AUTH_MODE`, `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET`,
  `ACCESS_TOKEN_MINUTES`, `REFRESH_TOKEN_DAYS`.

## Commands

```bash
# backend (Python 3.12, uv; Docker required for testcontainers; duckdb
# reads explore parquet artifacts read-only)
# graphrag>=3.1.2 pulls lancedb>=0.37, which ships no macOS x86_64 wheels:
# on Intel Macs `uv sync` fails — run backend gates in Docker instead. The
# named volumes keep the venv off the host tree and make reruns fast;
# testcontainers reaches the host daemon through the mounted socket:
#   docker run --rm -v "$PWD":/repo -w /repo/backend \
#     -v /var/run/docker.sock:/var/run/docker.sock \
#     -v graphrag-ui-uv-cache:/root/.cache/uv -v graphrag-ui-venv:/opt/venv \
#     -e UV_PROJECT_ENVIRONMENT=/opt/venv -e TESTCONTAINERS_RYUK_DISABLED=true \
#     ghcr.io/astral-sh/uv:python3.12-bookworm \
#     sh -c 'uv sync --frozen -q && uv run ruff check && uv run ruff format --check && uv run mypy && uv run pytest -q -m "not slow"'
cd backend && uv run pytest -v          # 802 tests with GRAPHRAG_API_KEY (796 fast); 6 slow tests fork the real graphrag CLI (4 need the key, skipped without it); fast only: uv run pytest -m "not slow"
cd backend && uv run ruff check
cd backend && uv run ruff format --check   # formatting is CI-enforced; `ruff format` to fix
cd backend && uv run mypy                  # src/ must stay clean; CI-enforced
cd backend && uv run pytest -m "not slow" --cov --cov-report=term-missing   # coverage; pyproject sets greenlet tracing (without it SQLAlchemy async under-reports ~13 points)
cd backend && uv run --with pip-audit pip-audit --desc --skip-editable --ignore-vuln PYSEC-2026-3740   # CI `audit` job (required); ignore list lives in ci.yml, dated, with the reason

# frontend (Node 24; jsdom+undici need >=22; explore graph renders via
# react-sigma + graphology, lazy-loaded as a separate build chunk; the
# project and admin pages are route-level chunks via components/lazyPage)
cd frontend && npm test                 # vitest run (374 tests)
cd frontend && npm run lint             # oxlint, --max-warnings=0 (any warning fails)
cd frontend && npx tsc -b --noEmit
cd frontend && npm run build

# deploy checks (compose needs .env for ${VAR:?}: cp .env.example .env, then
# set JWT_SECRET — .env.example ships it empty and the api rejects a
# placeholder at startup)
docker compose config
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config   # needs the proxy .env vars
docker compose build                     # catches Dockerfile drift (e.g. .npmrc must ship with npm ci)
helm lint deploy/helm/graphrag-ui
helm template deploy/helm/graphrag-ui > /dev/null
.github/scripts/compose-smoke.sh local   # CI `smoke`: boots the stack, signs in, uploads 2 MiB through nginx
.github/scripts/compose-smoke.sh proxy   # proxy overlay + test IdP, expects 401 on /api/*; SMOKE_PORT/SMOKE_OVERRIDE when 8080 is taken
```

## Working Rules

- TDD: failing test first, minimal implementation, green before commit.
- graphrag is pinned (`==3.1.2`, latest stable); do not bump without
  checking `graphrag_input/input_config.py` key names (`input.type`,
  `input.file_pattern` is a regex) — wrong keys are silently ignored
  (`extra="allow"`), so always read back and assert after writing
  `settings.yaml`. graphrag 3.1.2 declares `nltk~=3.9.0`; `[tool.uv]
  override-dependencies` lifts nltk to `>=3.10.3` for its advisories,
  and litellm (pinned `==1.92.0` by graphrag-llm) to the `1.92.2` patch
  release — on a graphrag bump, drop each override the new range admits.
- Dependency advisories: the CI `audit` job is required. An advisory
  with no fixed release that is not reachable here goes on its
  `--ignore-vuln` list with a dated reason; anything else is fixed
  (bump, or an override for a transitive pin).
- Layering is test-enforced (`backend/tests/test_layering.py`): a new
  graphrag import site or a cross-layer import fails the suite.
- `graphrag init` in a non-TTY subprocess needs `--model/--embedding`
  flags (typer prompts abort otherwise).
- API contract surface is the generated OpenAPI document (`openapi.json`,
  regenerated+diffed in CI); pydantic models live in `api/schemas.py` and
  per-route modules; frontend types come from `types.generated.ts`
  (regenerated via `npm run gen:types`; schemas.py docstring changes flow
  into it — always regen both)
