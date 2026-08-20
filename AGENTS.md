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
    All graphrag touchpoints live here (subprocess CLI only).
  - `api/` — FastAPI routes/schemas/auth; translate service errors to HTTP.
- DB schema changes go through alembic migrations only. Never edit tables
  by hand. `adapters/db.py` engine is lazy — never build engines at module
  import time.
- Environment variable names are fixed: `DATABASE_URL`, `WORKSPACES_DIR`,
  `JWT_SECRET`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`.

## Commands

```bash
# backend (Python 3.12, uv; Docker required for testcontainers)
cd backend && uv run pytest -v          # 79 tests; slow-marked ones fork the real graphrag CLI
cd backend && uv run ruff check

# frontend (Node 20+)
cd frontend && npm test                 # vitest
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
