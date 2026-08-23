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
- Documentation and code comments in English (zh-TW translations may be
  added under `docs/zh-TW/` later; English stays authoritative).
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
  `QUERY_CACHE_MB`, `QUERY_RATE_LIMIT_PER_HOUR`.

## Commands

```bash
# backend (Python 3.12, uv; Docker required for testcontainers; duckdb
# reads explore parquet artifacts read-only)
cd backend && uv run pytest -v          # 210 tests with GRAPHRAG_API_KEY (205 fast); 5 slow tests fork the real graphrag CLI and need the key; fast only: uv run pytest -m "not slow"
cd backend && uv run ruff check

# frontend (Node 24; jsdom+undici need >=22; explore graph renders via
# react-sigma + graphology, lazy-loaded as a separate build chunk)
cd frontend && npm test                 # vitest run (57 tests)
cd frontend && npx tsc -b --noEmit
cd frontend && npm run build

# deploy checks
docker compose config
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
- API responses follow the backend schemas in `api/schemas.py`; frontend
  `src/api/types.ts` mirrors them — keep both in sync.
