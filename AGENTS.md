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
  `QUERY_CACHE_MB`, `QUERY_RATE_LIMIT_PER_HOUR`, `AUTH_MODE`,
  `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET`.

## Commands

```bash
# backend (Python 3.12, uv; Docker required for testcontainers; duckdb
# reads explore parquet artifacts read-only)
cd backend && uv run pytest -v          # 242 tests with GRAPHRAG_API_KEY (237 fast); 5 slow tests fork the real graphrag CLI (3 need the key, skipped without it); fast only: uv run pytest -m "not slow"
cd backend && uv run ruff check

# frontend (Node 24; jsdom+undici need >=22; explore graph renders via
# react-sigma + graphology, lazy-loaded as a separate build chunk)
cd frontend && npm test                 # vitest run (61 tests)
cd frontend && npx tsc -b --noEmit
cd frontend && npm run build

# deploy checks (compose needs .env for ${VAR:?}: cp .env.example .env)
docker compose config
docker compose -f docker-compose.yml -f docker-compose.proxy-auth.yml config   # needs the proxy .env vars
docker compose build                     # catches Dockerfile drift (e.g. .npmrc must ship with npm ci)
helm lint deploy/helm/graphrag-ui
helm template deploy/helm/graphrag-ui > /dev/null
```

## Working Rules

- TDD: failing test first, minimal implementation, green before commit.
- graphrag is pinned (`==3.1.0`); do not bump without checking
  `graphrag_input/input_config.py` key names (`input.type`,
  `input.file_pattern` is a regex) — wrong keys are silently ignored
  (`extra="allow"`), so always read back and assert after writing
  `settings.yaml`.
- `graphrag init` in a non-TTY subprocess needs `--model/--embedding`
  flags (typer prompts abort otherwise).
- API contract surface is the generated OpenAPI document (`openapi.json`,
  regenerated+diffed in CI); pydantic models live in `api/schemas.py` and
  per-route modules; frontend types come from `types.generated.ts`
  (regenerated via `npm run gen:types`; schemas.py docstring changes flow
  into it — always regen both)
