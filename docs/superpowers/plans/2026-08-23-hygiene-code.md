# Hygiene Remediation Wave A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore clean-architecture spirit (services own transactions, routes translate), remove async-blocking I/O, and add two drift gates (alembic schema, OpenAPI types) per `docs/superpowers/specs/2026-08-23-hygiene-remediation-design.md` §4.

**Architecture:** Eight independent tasks on branch `feature/hygiene-code`. Backend refactors keep every existing test green (audit rows and API shapes are contract); new gates are fast tests plus one CI diff step per side. No new runtime dependencies; one frontend devDependency (`openapi-typescript`) and one committed generated file (`openapi.json`).

**Tech Stack:** FastAPI, SQLAlchemy async (asyncpg), alembic, pytest + testcontainers, pydantic v2, vitest/tsc, GitHub Actions.

## Global Constraints

- graphrag stays pinned `==3.1.0`; graphrag/duckdb imports ONLY in `adapters/graphrag_search.py` / `adapters/artifacts.py`.
- Services never import FastAPI, never raise `HTTPException`. Routes never call `db.commit()` after this wave (except adapters-internal commits). Error contract `{"detail": zh-TW}` for user-facing routes; admin/user routes currently return English details — keep each route's existing message text unchanged.
- Code comments English; Conventional Commits; TDD (failing test first); fast suites green before each commit: `cd backend && uv run pytest -q -m "not slow"` (Docker required — testcontainers) and for frontend-touching tasks `cd frontend && npx vitest run && npx tsc -b --noEmit`.
- No file may exceed 400 lines. Streaming uploads are NEVER materialized in memory (spec §8.2).
- Spec §4 is authoritative for task scope; spec §3 items are out of scope.

---

### Task 1: `domain/workspaces.py` — pure workspace path guard (spec A3)

**Files:**
- Create: `backend/src/graphrag_ui/domain/workspaces.py`
- Modify: `backend/src/graphrag_ui/services/projects.py:30-37` (replace `_ws_path`)
- Modify: every `_ws_path` importer — `services/{env_file,jobs,files,settings,query,explore,runner_loop}.py`, `api/jobs_routes.py:26`, `api/dry_run_routes.py:16`
- Test: `backend/tests/test_domain_workspaces.py` (new)

**Interfaces:**
- Produces: `domain.workspaces.workspace_path(root: Path, project_id) -> Path` (pure, raises `ValueError` on lexical escape) and `services.projects.ws_path(project_id) -> Path` (resolve + containment re-check; public, no underscore). All later tasks import `ws_path` from `services.projects`.

- [ ] **Step 1: Write failing domain tests**

```python
# backend/tests/test_domain_workspaces.py
from pathlib import Path

import pytest

from graphrag_ui.domain.workspaces import workspace_path


def test_workspace_path_joins_root():
    root = Path("/srv/ws")
    assert workspace_path(root, "9ba2c483-773c-4ba2-a4b8-71f457e9c13d") == \
        Path("/srv/ws/9ba2c483-773c-4ba2-a4b8-71f457e9c13d")


def test_workspace_path_rejects_lexical_escape():
    with pytest.raises(ValueError, match="escapes"):
        workspace_path(Path("/srv/ws"), "../../etc")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_domain_workspaces.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'graphrag_ui.domain.workspaces'`

- [ ] **Step 3: Implement the pure function**

```python
# backend/src/graphrag_ui/domain/workspaces.py
"""Pure workspace-path logic (spec A3).

Domain keeps no I/O and no config: this module only joins and lexically
validates. The service wrapper (services.projects.ws_path) owns
get_settings() and Path.resolve() — resolve() issues real syscalls and
can follow symlinks, so the post-resolve containment re-check lives there.
"""
from pathlib import Path


def workspace_path(root: Path, project_id) -> Path:
    """workspace dir for `project_id` under `root`.

    Lexical containment only: the unresolved candidate must stay inside
    `root`. Callers that need the on-disk truth resolve afterwards and
    re-check containment against the resolved root.
    """
    candidate = root / str(project_id)
    if not candidate.is_relative_to(root):
        msg = f"workspace path escapes workspaces dir: {candidate}"
        raise ValueError(msg)
    return candidate
```

- [ ] **Step 4: Domain tests pass**

Run: `cd backend && uv run pytest tests/test_domain_workspaces.py -v`
Expected: PASS (2)

- [ ] **Step 5: Replace `_ws_path` with the public wrapper**

In `services/projects.py`, replace lines 30-37 (`def _ws_path`) with:

```python
def ws_path(project_id: uuid.UUID) -> Path:
    """Resolved workspace dir; resolve can follow symlinks, so containment
    is re-asserted against the resolved root (spec A3, §10)."""
    root = Path(get_settings().workspaces_dir).resolve()
    path = workspace_path(root, project_id).resolve()
    if not path.is_relative_to(root):
        msg = f"workspace path escapes workspaces dir: {path}"
        raise ValueError(msg)
    return path
```

Add `from graphrag_ui.domain.workspaces import workspace_path` to the imports; delete the old `_ws_path` body entirely (clean cutover, no shim). Then update every importer (grep `_ws_path` under `backend/src`): switch `from graphrag_ui.services.projects import _ws_path` → `from graphrag_ui.services.projects import ws_path` and rename call sites. The two api modules (`jobs_routes.py`, `dry_run_routes.py`) import `ws_path` from `services.projects` — services own workspace paths; routes no longer import a private.

- [ ] **Step 6: Full fast suite green**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all pass, no unused imports.

- [ ] **Step 7: Commit**

```bash
git add -A backend/src backend/tests
git commit -m "refactor(domain): pure workspace_path guard; public ws_path service wrapper"
```

---

### Task 2: Transaction ownership — env/files services own audit + commit (spec A1)

**Files:**
- Modify: `backend/src/graphrag_ui/services/env_file.py` (make `set_env_key`/`delete_env_key` async, take `(session, project, key, value|None, actor_id)`)
- Modify: `backend/src/graphrag_ui/services/files.py:86-124,151-159` (`save_file`/`delete_file` take `(session, ..., actor_id)`; audit+commit inside)
- Modify: `backend/src/graphrag_ui/api/env_routes.py:74-84,93-100`, `backend/src/graphrag_ui/api/files_routes.py:96-108,130-139` (drop audit/commit, pass session+actor)
- Test: `backend/tests/test_env.py`, `backend/tests/test_files.py` (extend)

**Interfaces:**
- Consumes: `audit(session, actor_id, action, target_type, target_id, payload)` from `services.audit` (unchanged).
- Produces (later tasks / routes rely on these):
  - `async def set_env_key(session, project, key: str, value: str, actor_id) -> None`
  - `async def delete_env_key(session, project, key: str, actor_id) -> None` (still raises `KeyError`)
  - `async def save_file(session, project, filename: str, source, actor_id) -> tuple[str, int]` — `source` keeps its streaming contract (`async read(n)`; never materialized)
  - `async def delete_file(session, project, filename: str, actor_id) -> int`
- Ordering invariant (spec A1): audit row flushed inside the transaction BEFORE `commit()`. Payload-known-first functions: audit+flush → external work → commit. `save_file` (size known only after streaming): stream tmp → audit+flush → quota check → atomic rename → commit.

- [ ] **Step 1: Write the failing ordering tests (add to test_files.py)**

```python
async def test_upload_rollback_leaves_no_audit_row_when_stream_fails(
        session, project, admin_user):
    # A reader that raises mid-stream: save_file must roll back the audit
    # row AND remove the tmp file; the workspace stays clean.
    class Boom:
        async def read(self, n):
            raise RuntimeError("stream broke")

    with pytest.raises(RuntimeError, match="stream broke"):
        await files_service.save_file(session, project, "ok.txt",
                                      Boom(), actor_id=admin_user.id)
    input_dir = ws_path(project.id) / "input"
    assert not list(input_dir.glob(".tmp-*"))
    n_rows = (await session.execute(
        select(AuditLog).where(AuditLog.action == "file.uploaded"))).scalars()
    assert list(n_rows) == []
```

Use the session/project/admin fixtures already used by the direct-service tests in `test_files.py` (match their names exactly; if the file's fixtures differ, follow its pattern). Mirror one env test in `test_env.py`: monkeypatch `env_file._atomic_write` to raise `OSError`, call `await set_env_key(session, project, "GRAPHRAG_API_KEY", "x", admin_user.id)` inside `pytest.raises(OSError)`, assert the `env.key_set` audit count is 0 and the `.env` on disk is unchanged.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_files.py tests/test_env.py -q`
Expected: FAIL — `TypeError: save_file() got an unexpected keyword argument 'actor_id'` (and audit rows appear on failure today).

- [ ] **Step 3: Rewrite the four service functions**

`services/env_file.py` — change signatures; wrap the existing pure logic:

```python
async def set_env_key(session, project: Project, key: str, value: str,
                      actor_id) -> None:
    """Upsert key=value AND audit it, one transaction (spec A1)."""
    _validate(key, value)          # extracted: _KEY_RE + single-line checks
    try:
        await audit(session, actor_id, "env.key_set", "project",
                    str(project.id), {"key": key})
        await session.flush()
        _atomic_write(project, _upsert_lines(project, key, value))
        await session.commit()
    except Exception:
        await session.rollback()
        raise
```

`delete_env_key` identical shape (`KeyError` still raised before any write). Split the current function bodies into pure helpers (`_validate`, `_upsert_lines`, `_remove_lines`) so the transaction wrapper stays small.

`services/files.py save_file` — keep quota snapshot, streaming, and cap logic byte-for-byte; move the audit/commit in:

```python
    name = _safe_name(project.input_file_type, filename)
    base_usage = await usage_bytes(project)          # Task 4 keeps this async
    input_dir = ws_path(project.id) / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    tmp = input_dir / f".tmp-{uuid.uuid4().hex}"
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := await source.read(_CHUNK_BYTES):
                size += len(chunk)
                if size > max_file_bytes():
                    raise FileTooLargeError(...)
                out.write(chunk)
        if base_usage + size > quota_bytes():
            raise QuotaExceededError(...)            # before audit: no row
        await audit(session, actor_id, "file.uploaded", "project",
                    str(project.id), {"name": name, "size": size})
        await session.flush()
        os.replace(tmp, input_dir / name)
        await session.commit()
        return name, size
    except Exception:
        await session.rollback()
        raise
    finally:
        tmp.unlink(missing_ok=True)
```

`delete_file`: `size = target.stat().st_size` (raises `FileNotFoundError` first) → `await audit(...)` + `flush()` → `target.unlink()` → `commit()`; `except: rollback; raise`. Residual (spec A1, accepted): commit failure after unlink loses the audit row.

- [ ] **Step 4: Update the two route modules**

`env_routes.py`: handlers become parse/permission → `await set_env_key(db, project, key, value, user.id)` → translate `ValueError` (400) / `KeyError` (404); delete the `audit(...)`/`db.commit()` lines (81-83, 97-99). `files_routes.py`: same for upload (audit/commit lines 105-107 move into the service; handler keeps the FileServiceError/413 translation) and delete (136-138). Remove now-unused `audit` imports.

- [ ] **Step 5: Suites green (audit-row tests must pass unchanged)**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all pass. Existing audit assertions (env.key_set/file.uploaded payloads) unchanged — they are contract.

- [ ] **Step 6: Commit**

```bash
git add -A backend/src backend/tests
git commit -m "refactor(services): env/files services own audit and transaction (A1 ordering)"
```

---

### Task 3: `users_routes` stops querying (spec A2)

**Files:**
- Modify: `backend/src/graphrag_ui/services/users.py` (add query + guard functions)
- Modify: `backend/src/graphrag_ui/api/users_routes.py` (handlers shrink to parse→call→translate)
- Test: `backend/tests/test_users_api.py` (extend; existing 400/404/last-admin tests must stay green)

**Interfaces:**
- Produces:
  - `class UserNotFound(LookupError)` / `class SelfRoleChangeError(ValueError)` / `class LastActiveAdminError(ValueError)` in `services/users.py`
  - `async def get_user(session, user_id) -> User` (raises `UserNotFound`)
  - `async def list_users_ordered(session) -> list[User]` (`created_at, id`)
  - `async def list_users_by_email(session) -> list[User]` (`email`)
  - `async def patch_user_guarded(session, admin: User, user_id, *, display_name, role, is_active) -> User` (raises the three above, then delegates to existing `update_user`)

- [ ] **Step 1: Write failing tests**

Three service-level tests in `test_users_api.py` (following its existing direct-service style): `get_user` unknown id → `UserNotFound`; `patch_user_guarded` self role change → `SelfRoleChangeError`; demote of the only active admin → `LastActiveAdminError`.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_users_api.py -q`
Expected: FAIL — names not importable.

- [ ] **Step 3: Implement in services/users.py**

Move `_other_active_admin_count` from `users_routes.py:32-36` into `services/users.py`, then add the four functions. `patch_user_guarded` body = the exact rule block from `users_routes.py:68-76` (self-change, demotes computation, last-admin count) with `HTTPException` raises replaced by the domain errors, then `return await update_user(session, user, display_name=display_name, role=role, is_active=is_active, actor_id=admin.id)`.

- [ ] **Step 4: Shrink the handlers**

`users_routes.py`: `list_users` → `list_users_ordered`; `patch_user` → try `patch_user_guarded` except `UserNotFound`→404 / `SelfRoleChangeError`→400 "cannot change your own role or active status" / `LastActiveAdminError`→400 "cannot demote or deactivate the last active admin" (identical message strings); `post_reset_password` → `get_user` + translate 404; `list_users_brief` → `list_users_by_email`. Drop `select/func/User` imports that become unused (keep `IntegrityError` translation for POST).

- [ ] **Step 5: Suite green**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all pass (existing API tests pin the 400/404 messages).

- [ ] **Step 6: Commit**

```bash
git add -A backend/src backend/tests
git commit -m "refactor(services): users queries and admin guards move out of routes"
```

---

### Task 4: Async handlers stop blocking — `to_thread` for unbounded scans (spec A4)

**Files:**
- Modify: `backend/src/graphrag_ui/services/files.py` (`usage_bytes` async; `list_files` scan off-loop)
- Modify: `backend/src/graphrag_ui/services/jobs.py:27-54,79-109` (enqueue + preflight disk_usage; `_tree_bytes`)
- Modify: `backend/src/graphrag_ui/api/health_routes.py:21-41` (`ready`)
- Modify: `backend/src/graphrag_ui/api/files_routes.py:118` (await usage_bytes)
- Test: existing suites; add one behavior pin

**Interfaces:**
- Produces: `async def usage_bytes(project) -> int` (was sync). Criterion (spec A4): unbounded tree walks / whole-disk stats go through `asyncio.to_thread`; bounded single-file ops stay sync (`settings.py:46,79`, `env_file.py:27,39`, `adapters/job_logs.py:15`, `save_file`'s write loop — explicitly unchanged).

- [ ] **Step 1: Write the failing pin (test_files.py)**

```python
async def test_usage_bytes_is_awaitable(project):
    # Regression pin: usage_bytes must be a coroutine function — a sync
    # rglob on the event loop froze large-workspace requests (spec A4).
    import inspect
    assert inspect.iscoroutinefunction(files_service.usage_bytes)
    n = await files_service.usage_bytes(project)
    assert n >= 0
```

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_files.py -q`
Expected: FAIL — `iscoroutinefunction` is False.

- [ ] **Step 3: Implement**

`files.py`: `async def usage_bytes(project)` → `return sum(await asyncio.to_thread(f) for f in ...)`? No — one hop: move the body to `_usage_bytes_sync(project)` (calls `_dir_size` twice) and `return await asyncio.to_thread(_usage_bytes_sync, project)`; fix the internal `save_file` caller to `await usage_bytes(project)`. `list_files`: extract `_scan_input(input_dir) -> list[dict]` (the iterdir/stat/sort block) and `return await asyncio.to_thread(_scan_input, input_dir)`; drop the early-return by making `_scan_input` handle a missing dir. `jobs.py`: `free = (await asyncio.to_thread(shutil.disk_usage, ws_root)).free` in `enqueue`; `_tree_bytes` becomes sync (drop `async`) and `preflight` does `"cache_bytes": await asyncio.to_thread(_tree_bytes, root / "cache")` plus the same `to_thread` wrap for its `disk_usage`. `health_routes.ready`: `disk_free_mb = (await asyncio.to_thread(shutil.disk_usage, ws_root)).free // (1024 * 1024)`. `files_routes.py:118`: `usage_bytes=await files_service.usage_bytes(project)`.

- [ ] **Step 4: Suite green**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src backend/tests
git commit -m "fix(services): move unbounded disk scans off the event loop (to_thread)"
```

---

### Task 5: Schema drift gate — alembic head vs `Base.metadata` (spec A5.1)

**Files:**
- Create: `backend/tests/test_schema_drift.py`
- Test-only task; no production code.

**Interfaces:**
- Consumes: session-scoped `migrated_db` fixture (`conftest.py:34-41`), `adapters.db.make_engine`, `adapters.models.Base`, `migrations/env.py:75-76` `run_sync` pattern. No sync DB driver added.

- [ ] **Step 1: Write the test**

```python
"""Schema drift gate (spec A5.1): alembic head must match Base.metadata.

Fail categories: add/remove table, add/remove column, type change,
nullability change. Ignored (known autogenerate noise): server_default,
index/constraint naming and rendering. Table-level drift was already
caught accidentally by conftest's TRUNCATE; this gate adds the
column/type/nullability layer.
"""
from alembic.autogenerate import compare_metadata

from graphrag_ui.adapters.db import make_engine
from graphrag_ui.adapters.models import Base

_FAIL_KINDS = {"add_table", "remove_table", "add_column", "remove_column",
               "modify_type", "modify_nullable"}


async def test_alembic_head_matches_metadata(migrated_db):
    engine = make_engine(migrated_db)
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda c: compare_metadata(c, Base.metadata))
    finally:
        await engine.dispose()
    real = [d for d in diffs if d[0] in _FAIL_KINDS]
    assert real == [], f"schema drift (migrate or revert the model): {real}"
```

(Use the repo's async-test convention as seen in neighboring tests — no extra marker if `asyncio_mode=auto`.)

- [ ] **Step 2: Run — expect PASS and prove the gate bites**

Run: `cd backend && uv run pytest tests/test_schema_drift.py -v`
Expected: PASS against the current tree. Then temporarily add a dummy column to any model (`User.hygiene_probe = mapped_column(String(8), nullable=True)`), re-run: Expected FAIL listing `add_column`. Revert the probe.

- [ ] **Step 3: Full fast suite + commit**

```bash
cd backend && uv run pytest -q -m "not slow"
git add backend/tests/test_schema_drift.py
git commit -m "test(db): schema drift gate — alembic head vs Base.metadata"
```

---

### Task 6: API types drift gate — `openapi.json` + `types.generated.ts` (spec A5.2)

**Files:**
- Create: `backend/scripts/gen_openapi.py`
- Create: `openapi.json` (repo root, committed)
- Create: `frontend/src/api/types.generated.ts` (committed)
- Modify: `frontend/src/api/types.ts` (re-export layer), `frontend/package.json` (devDep + script), `.github/workflows/ci.yml` (two diff steps)
- Test: `backend/tests/test_openapi_contract.py`

**Interfaces:**
- Produces: `types.ts` KEEPS every current export name (components import `Job`, `Project`, … unchanged). Generated `components["schemas"][...]` types back the ones with backend pydantic models; the 8 contract-less types (`ArtifactPage`, `ArtifactDetail`, `GraphNode`, `GraphEdge`, `GraphData`, `Citation`, `QueryTimings`, `QueryMethod`) and `JobStatusColor` stay hand-written in `types.ts`, each tagged `// no backend response_model yet — hand-maintained (spec A5.2)`.
- CI: backend job regenerates `openapi.json`, `git diff --exit-code`; frontend job regenerates `types.generated.ts`, `git diff --exit-code`. Jobs stay parallel (no `needs:`).

- [ ] **Step 1: Response-model ratchet test (backend)**

```python
# backend/tests/test_openapi_contract.py
"""Pin the endpoints that still answer without a response_model (spec
A5.2 ratchet): the set may only shrink. New endpoints MUST declare one."""
from graphrag_ui.main import create_app
from fastapi.routing import APIRoute

KNOWN_UNTYPED = {
    # fill from the first failing run — see Step 2
}


def test_untyped_endpoints_ratchet():
    app = create_app()
    untyped = {
        f"{sorted(r.methods - {'HEAD'})[0]} {r.path}"
        for r in app.routes if isinstance(r, APIRoute) and r.response_model is None
    }
    assert untyped == KNOWN_UNTYPED, (
        f"response_model debt changed: {untyped ^ KNOWN_UNTYPED}")
```

- [ ] **Step 2: Run, pin the set**

Run: `cd backend && uv run pytest tests/test_openapi_contract.py -v`
Expected: FAIL printing the actual set (explore list/detail/graph, query POST + stream, health/ready, and any others). Paste it into `KNOWN_UNTYPED`; re-run → PASS. Commit later with the rest.

- [ ] **Step 3: OpenAPI export script**

```python
# backend/scripts/gen_openapi.py
"""Regenerate ../openapi.json (committed; CI diffs it — spec A5.2)."""
import json
from pathlib import Path

from graphrag_ui.main import create_app

out = Path(__file__).resolve().parents[2] / "openapi.json"
spec = create_app().openapi()
out.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
print(f"wrote {out}")
```

Run: `cd backend && uv run python scripts/gen_openapi.py` → commit `openapi.json`.

- [ ] **Step 4: Frontend codegen + re-export layer**

`cd frontend && npm install -D openapi-typescript` (pin whatever installs; record exact version). Add script `"gen:types": "openapi-typescript ../openapi.json -o src/api/types.generated.ts"`; run it. Rewrite `types.ts`: for each export whose backend model exists (Job←`JobOut`, LastRun←`LastRunOut`, Preflight←`PreflightOut`, User←`UserOut`, UserBrief←`UserBriefOut`, Project←`ProjectOut`, FileEntry/FilesOut/EnvKeyOut/SettingsOut/SettingsVersionOut/SettingsVersionDetail/Member + create/update bodies ← their route models — map by reading `openapi.json` components), replace the interface with `export type Job = components["schemas"]["JobOut"];` style aliases plus `import type { components } from "./types.generated";`. Keep the 8 + `JobStatusColor` hand-written with the marker comment. Export names must not change.

- [ ] **Step 5: Frontend green on generated layer**

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build`
Expected: all pass. If a generated field type mismatches a component's usage, fix the component — the backend shape is the contract.

- [ ] **Step 6: CI diff steps**

`.github/workflows/ci.yml` backend job, after `uv run pytest`:

```yaml
      - run: uv run python scripts/gen_openapi.py
      - run: git diff --exit-code ../openapi.json
```

frontend job, after `npm run build`:

```yaml
      - run: npm run gen:types
      - run: git diff --exit-code src/api/types.generated.ts
```

- [ ] **Step 7: Full verify + commit**

Run: `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
```bash
git add -A backend frontend openapi.json .github
git commit -m "test(api): OpenAPI contract gate — committed openapi.json + generated TS types"
```

---

### Task 7: Real-corpus fixtures converge + silent-skip guard (spec A6)

**Files:**
- Create: `backend/tests/real_corpus_fixtures.py`
- Modify: `backend/tests/test_real_corpus_query.py:66-98`, `test_real_corpus_jobs.py:73-98`, `test_real_corpus_explore.py:24`
- Test: `backend/tests/test_real_corpus_guard.py` (fast, runs without the key)

**Interfaces:**
- Produces: shared fixtures `real_corpus_app`, `real_corpus_client`, `ws_root`, `DOCS` in `real_corpus_fixtures.py`, each test module binds them under its local names (`query_app = real_corpus_app` style aliases so existing test bodies are untouched).

- [ ] **Step 1: Write the failing guard (fast, no key needed)**

```python
# backend/tests/test_real_corpus_guard.py
"""Fast guard against the PR#5 silent-skip failure mode (spec A6): if a
real-corpus module's fixture import is deleted, pytest silently skips
its tests. Object identity fails loudly instead. No pytest internals."""
import real_corpus_fixtures as helper
import test_real_corpus_explore
import test_real_corpus_jobs
import test_real_corpus_query


def test_real_corpus_modules_bind_shared_fixtures():
    for mod in (test_real_corpus_query, test_real_corpus_jobs,
                test_real_corpus_explore):
        assert mod.query_client is helper.real_corpus_client, mod.__name__
        marks = {m.name for m in getattr(mod, "pytestmark", [])}
        assert "slow" in marks, mod.__name__



def test_modules_share_the_common_pytestmark():
    for mod in (test_real_corpus_query, test_real_corpus_jobs,
                test_real_corpus_explore):
        assert mod.pytestmark == helper.pytestmark, mod.__name__
```

The key-gate (`skipif` no `GRAPHRAG_API_KEY`) and the `slow` mark live only in `real_corpus_fixtures.py`; every real-corpus module binds them by sharing that exact `pytestmark` object, so a module that re-declares or drops them fails this test.

- [ ] **Step 2: Run to verify failure**

Run: `cd backend && uv run pytest tests/test_real_corpus_guard.py -q`
Expected: FAIL — `real_corpus_fixtures` does not exist.

- [ ] **Step 3: Extract the helper; rewire three modules**

Move `ws_root`, the env-bootstrapping app fixture, the client fixture (rename to `real_corpus_app`/`real_corpus_client`), and `DOCS` into `real_corpus_fixtures.py` verbatim; export `pytestmark` (the slow+key-gate marks). In each test module: delete the local copies, `from real_corpus_fixtures import real_corpus_app as query_app, real_corpus_client as query_client, ws_root, DOCS, pytestmark` (module-appropriate alias; jobs module aliases to its own names; explore module's cross-module `noqa` import at line 24 disappears).

- [ ] **Step 4: Guard + fast suite green; slow plan sanity**

Run: `cd backend && uv run pytest tests/test_real_corpus_guard.py -q -m "not slow" && uv run pytest -q -m "not slow" && uv run ruff check`
Expected: all pass. Also run `uv run pytest --setup-plan -q tests/test_real_corpus_query.py` with a dummy `GRAPHRAG_API_KEY=x`: the three slow tests must appear as setup lines (not skips).

- [ ] **Step 5: Commit**

```bash
git add -A backend/tests
git commit -m "test(corpus): shared real-corpus fixtures + identity guard against silent skips"
```

---

### Task 8: Small convergences — shared service error base + `bodyOf`/`detailOf` (spec A7)

**Files:**
- Create: `backend/src/graphrag_ui/services/errors.py`
- Modify: `backend/src/graphrag_ui/services/query.py:45-52` (`QueryError`), `services/explore.py:37-47` (`ExploreReadError`), the `查詢中斷` literal sites
- Modify: `frontend/src/api/client.ts` (add `bodyOf`/`detailOf`), the 5 string-`detailOf` components + `frontend/src/components/SettingsPanel.tsx`
- Test: `backend/tests/test_query_service.py` (or nearest) type-level test; `frontend/src/api/__tests__/client.test.ts`

**Interfaces:**
- Produces: `services.errors.ServicePipelineError(code, detail="")` base; `INTERRUPTED_DETAIL = "查詢中斷"`; `QueryError`/`ExploreReadError` subclass it (public behavior identical). Frontend: `export async function bodyOf(r: Response): Promise<Record<string, unknown>>` and `export function detailOf(r: Response, fallback: string): Promise<string>` in `client.ts`.

- [ ] **Step 1: Backend failing test**

```python
def test_query_errors_share_base():
    from graphrag_ui.services.errors import ServicePipelineError
    from graphrag_ui.services.explore import ExploreReadError
    from graphrag_ui.services.query import QueryError

    assert issubclass(QueryError, ServicePipelineError)
    assert issubclass(ExploreReadError, ServicePipelineError)
    e = QueryError("search", "boom")
    assert (e.code, e.detail) == ("search", "boom")
```

- [ ] **Step 2: Verify failure, implement**

Run: `cd backend && uv run pytest tests/test_query_service.py -q` → FAIL (no `errors` module). Create `services/errors.py`:

```python
"""Shared base for service pipeline errors (spec A7).

code names the failing step; detail is server-log-only material — routes
return fixed zh-TW messages, never these strings.
"""
INTERRUPTED_DETAIL = "查詢中斷"


class ServicePipelineError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail
```

`QueryError(ServicePipelineError)` drops its own `__init__`; `ExploreReadError(ServicePipelineError)` keeps `tail` as an alias property for `detail` (its route/logs read `.tail`). Replace `查詢中斷` literals in `services/query.py` with `INTERRUPTED_DETAIL` (grep to find every site). Suite + ruff green; commit `refactor(services): shared ServicePipelineError base`.

- [ ] **Step 3: Frontend failing test**

```typescript
// frontend/src/api/__tests__/client.test.ts
import { detailOf } from "../client";

test("detailOf surfaces zh-TW detail verbatim", async () => {
  const r = new Response(JSON.stringify({ detail: "找不到該筆資料" }), { status: 404 });
  expect(await detailOf(r, "fallback")).toBe("找不到該筆資料");
});

test("detailOf falls back on non-JSON body", async () => {
  const r = new Response("<html>", { status: 502 });
  expect(await detailOf(r, "fallback")).toBe("fallback");
});
```

- [ ] **Step 4: Implement + swap call sites**

In `client.ts`:

```typescript
export async function bodyOf(r: Response): Promise<Record<string, unknown>> {
  try { return (await r.json()) as Record<string, unknown>; } catch { return {}; }
}

export async function detailOf(r: Response, fallback: string): Promise<string> {
  const body = await bodyOf(r);
  return typeof body.detail === "string" ? body.detail : fallback;
}
```

Grep `detailOf` across `frontend/src/components` — the 5 copies that return `Promise<string>` delete their local definitions and import from `../api/client`; `SettingsPanel.tsx:24`'s variant (returns the whole body) is replaced by `bodyOf`. Existing component tests (57) stay green.

Run: `cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add -A backend/src backend/tests frontend/src
git commit -m "refactor: shared ServicePipelineError; single bodyOf/detailOf error helpers"
```

---

## Final verification (whole wave)

- `cd backend && uv run pytest -q -m "not slow" && uv run ruff check`
- `cd frontend && npx vitest run && npx tsc -b --noEmit && npm run build`
- `cd backend && uv run python scripts/gen_openapi.py && git diff --exit-code ../openapi.json`
- Optional with key: `GRAPHRAG_API_KEY=$(...) uv run pytest -q -m slow` (real-corpus suite still sound after Task 7)
- Push `feature/hygiene-code`, CI green (backend incl. openapi diff, frontend incl. codegen diff, helm), scoped wave review, PR rebase-merge.

## Self-Review (done)

- Spec §4 coverage: A1→Task 2, A2→Task 3, A3→Task 1, A4→Task 4, A5.1→Task 5, A5.2→Task 6, A6→Task 7, A7→Task 8. §3 exclusions untouched. N1 two-shape ordering embedded in Task 2 code; N2 `source` streaming preserved; N3 delete residual documented in Task 2; N4 `run_sync` in Task 5; N5 committed-`openapi.json` parallel CI in Task 6; N8 identity guard in Task 7.
- Type consistency: `ws_path` public name used across tasks; `usage_bytes` async signature matches Task 2's `await usage_bytes(project)`; `real_corpus_app`/`real_corpus_client` alias scheme consistent.
- Placeholder scan: Task 2 Step 1 contains an explicit "replace the placeholder body" instruction with the concrete assertion pattern (session fixture + Boom reader + AuditLog count) rather than vague guidance; Task 6 Step 2 pins the ratchet set from the first run — both are pinned procedures, not TBDs.
