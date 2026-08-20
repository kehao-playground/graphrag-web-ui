# Foundation-B Implementation Plan (GraphRAG Web UI Phase 2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver file upload with quotas, the dual-mode settings editor with hash-based optimistic locking and version history, per-key `.env` management, and the dry-run validation endpoint — everything project members need to prepare a workspace for indexing.

**Architecture:** Follows Foundation-A layering (`domain`/`services`/`adapters`/`api`). All workspace file operations go through the existing `_ws_path` helper with resolve-and-contain checks; graphrag runs only as a subprocess from adapters. Spec: `docs/superpowers/specs/2026-08-19-graphrag-web-ui-design.md` (§6.1, §6.2, §6.5, §8.3, §10).

**Tech Stack:** Unchanged from Foundation-A — FastAPI + SQLAlchemy 2 async + alembic (backend/), React 19 + AntD 6 + TanStack Query v5 (frontend/), graphrag==3.1.0 pinned.

## Global Constraints

- **Language (per CONTRIBUTING.md / AGENTS.md):** Conventional Commits with English subject and body; code comments and identifiers in English. When a file you touch still has zh-TW comments, migrate the comments in the sections you modify — no repo-wide rewrites.
- **Layering:** `domain/` pure; `services/` must not import FastAPI or raise `HTTPException` (raise domain errors, let routes map to HTTP); all graphrag touchpoints in `adapters/` via subprocess only; schema changes via alembic only.
- **Environment variables (fixed names):** existing `DATABASE_URL`, `WORKSPACES_DIR`, `JWT_SECRET`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` plus new `UPLOAD_MAX_FILE_MB` (default 50), `PROJECT_QUOTA_MB` (default 5000) — spec §8.3 amended in Task 2.
- **Path safety:** every workspace file path derives from `_ws_path(project_id)` and must pass `resolve()` + `is_relative_to(workspaces_root)` before use (spec §10).
- **Secrets:** `.env` values are never returned in plaintext; masked only (spec §6.1).
- **Permissions (spec §5 matrix):** upload/delete/settings/env/dry-run require admin or project role owner/editor ("editor+"); listing files and reading settings require viewer+.
- **graphrag is pinned (`==3.1.0`); `settings.yaml` keys are `input.type` / `input.file_pattern` (regex) and wrong keys are silently ignored (`extra="allow"`)** — any code that writes YAML must read back and assert.
- pytest `asyncio_mode=auto`; every task ends green (full backend + frontend suites, `ruff check`, `npm run build` where frontend touched) with a conventional commit.

## Existing Interfaces (Foundation-A, exact symbols — do not re-invent)

- `backend/src/graphrag_ui/api/deps.py`: `DbSession`, `CurrentUser`, `AdminUser` annotated deps; `get_current_user`.
- `backend/src/graphrag_ui/services/projects.py`: `_ws_path(project_id: uuid.UUID) -> Path` (resolve+contain enforced), `get_project_role(session, project_id, user_id) -> str | None`, `list_projects`, `delete_project`.
- `backend/src/graphrag_ui/domain/permissions.py`: `Action`, `can(user_role, is_active, action, project_role=None) -> bool` (callers pass `Action` members).
- `backend/src/graphrag_ui/services/audit.py`: `audit(session, actor_id, action, target_type, target_id, payload=None)` — adds, never commits; caller owns the transaction.
- `backend/src/graphrag_ui/api/projects_routes.py`: `MemberIn` (`Literal["owner","editor","viewer"]` at lines 58-59 — Task 1 changes this), `register_projects_routes(app)` with in-function `APIRouter(prefix="/api/projects", dependencies=[Depends(get_current_user)])`, `_forbidden()` helper, `get_initializer` Depends-injection pattern.
- `backend/src/graphrag_ui/adapters/workspace.py`: `WorkspaceInitializer` protocol, `GraphragInitInitializer`, `WorkspaceInitError`, `_FILE_PATTERNS`.
- `backend/src/graphrag_ui/config.py` `Settings`: add the two new fields in Task 2; `@lru_cache get_settings()`.
- `backend/tests/conftest.py` fixtures: `client` (LifespanManager, bootstrap admin `admin@test.local`/`admin-pass-123`, `WORKSPACES_DIR` → tmp), `db_session`, `clean_db`; helper pattern `_activate(client, email, initial_pw, new_pw)` in `tests/test_projects.py`.
- Frontend: `api()` in `src/api/client.ts` (401 single-flight refresh retry), `detailOf(r, fallback)` error pattern, `ProjectDetail.tsx` with `DISABLED_TABS` (keys `files`, `settings` become real in Tasks 6-7), query keys `["projects", id]`, `["projects", id, "members"]`, `["users"]`; `useAuth().user`.

## File Structure Overview

```
backend/
  src/graphrag_ui/
    config.py                      # + upload_max_file_mb, project_quota_mb (Task 2)
    services/files.py              # NEW: whitelist, quota, save/list/delete (Task 2)
    services/settings.py           # NEW: hash-locked read/write + versions (Task 3)
    services/env_file.py           # NEW: .env per-key masked ops (Task 4)
    adapters/workspace.py          # + dry_run() subprocess (Task 5)
    adapters/models.py             # + SettingsVersion (Task 3)
    api/projects_routes.py         # MemberIn restriction (Task 1)
    api/files_routes.py            # NEW (Task 2)
    api/settings_routes.py         # NEW (Task 3)
    api/env_routes.py              # NEW (Task 4)
    api/dry_run_routes.py          # NEW (Task 5)
  migrations/versions/             # + settings_versions (Task 3)
  tests/test_files.py, test_settings.py, test_env.py, test_dry_run.py, test_projects.py (extended Task 1)
frontend/
  src/api/client.ts                # FormData fix (Task 6)
  src/api/types.ts                 # + FileEntry, FilesOut, EnvOut, SettingsOut... (Tasks 6-7)
  src/components/FilesPanel.tsx    # NEW (Task 6)
  src/components/SettingsPanel.tsx # NEW (Task 7)
  src/pages/ProjectDetail.tsx      # wire tabs (Tasks 6-7)
deploy/helm/graphrag-ui/values.yaml # PG16 tag (Task 1), new env vars (Task 8)
docs/superpowers/specs/…design.md  # §5 owner note (Task 1), §8.3 env names (Task 2)
```

---

### Task 1: Backend hardening — regression tests, single-owner restriction, PG16 alignment

**Files:**
- Modify: `backend/tests/test_projects.py` (+3 tests), `backend/src/graphrag_ui/api/projects_routes.py` (MemberIn), `frontend/src/pages/ProjectDetail.tsx` (add-role options), `deploy/helm/graphrag-ui/values.yaml` (PG tag), spec §5 note.

**Interfaces:**
- Produces: `MemberIn.role: Literal["editor", "viewer"]` (PUT with `"owner"` → 422); chart Postgres aligned to 16.x; three permanent regression tests.

- [ ] **Step 1: Write failing tests** (append to `backend/tests/test_projects.py`; reuse `_setup_two_users`/`_activate` helpers):

```python
async def test_delete_project_cascades_members(client, db_session):
    from sqlalchemy import select
    from graphrag_ui.adapters.models import ProjectMember
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "Cascade", "input_file_type": "text"})).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                     json={"role": "viewer"})
    assert (await db_session.execute(
        select(ProjectMember).where(ProjectMember.project_id == pid))).scalars().all(), "precondition: members exist"
    db_session.expire_all()  # detach cache so cascade is observed fresh
    await client.delete(f"/api/projects/{pid}", headers=alice)
    rows = (await db_session.execute(
        select(ProjectMember).where(ProjectMember.project_id == pid))).scalars().all()
    assert rows == []


async def test_init_failure_leaves_no_row(client, monkeypatch):
    from graphrag_ui.adapters.workspace import WorkspaceInitError
    from graphrag_ui.api import projects_routes
    class ExplodingInitializer:
        async def init(self, root, input_file_type):
            raise WorkspaceInitError("simulated graphrag init failure")
    monkeypatch.setattr(projects_routes, "get_initializer", lambda: ExplodingInitializer())
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    r = await client.post("/api/projects", headers=alice, json={
        "name": "Exploder", "input_file_type": "text"})
    assert r.status_code == 500
    assert r.json() == {"detail": "graphrag init failed"}
    names = [p["name"] for p in (await client.get("/api/projects", headers=alice)).json()]
    assert "Exploder" not in names  # rollback left no residual row


async def test_owner_role_not_grantable(client):
    admin = await _setup_two_users(client)
    alice = await _activate(client, "alice@test.local", "alice-pass-1", "alice-pass-2")
    pid = (await client.post("/api/projects", headers=alice, json={
        "name": "Solo", "input_file_type": "text"})).json()["id"]
    users = (await client.get("/api/admin/users", headers=admin)).json()
    bob_id = next(u["id"] for u in users if u["email"] == "bob@test.local")
    r = await client.put(f"/api/projects/{pid}/members/{bob_id}", headers=alice,
                         json={"role": "owner"})
    assert r.status_code == 422  # owner is fixed to the creator (single-owner policy)
```

(Last-admin 400 test: extend `tests/test_users.py` — create second admin via PATCH, then PATCH `is_active=False` on the *original* while the second is inactive → 400. Write it too.)

- [ ] **Step 2:** Run `uv run pytest tests/test_projects.py tests/test_users.py -v` — the owner-grant test fails (200 today), cascade/init-fail should pass already (they pin existing behavior; if any fails, that is a real Foundation-A bug — fix the product code, not the test).

- [ ] **Step 3: Implement.** `MemberIn.role: Literal["editor", "viewer"]`. Frontend: in `ProjectDetail.tsx` the add-member role select uses `ROLES` — change the *add flow* options to `editor|viewer` only (keep `owner` rendering in the members table). Also remove the now-dead owner option from `ROLE_OPTIONS` used by `Select` when adding.

- [ ] **Step 4: Spec amendment** (surgical, keep the doc's zh-TW): §5 after the permission matrix add one line: `- 擁有者為單一且固定(建立者);成員角色僅可授予 editor/viewer(2026-08-20 需求方裁定)`.

- [ ] **Step 5: PG alignment** — `deploy/helm/graphrag-ui/values.yaml` set `postgresql.image.tag: "16.8.0"` (bitnami postgresql 18.x chart accepts PG16 tags; verify `helm lint` + `helm template` show `postgresql:16.8.0`).

- [ ] **Step 6:** Full suites green (`uv run pytest -v`, `ruff check`, frontend `npm test`/`tsc`/`build`), then commit:

```bash
git commit -m "fix: enforce single project owner and align postgres versions; add regression tests"
```

---

### Task 2: File upload/list/delete backend with whitelist and quota

**Files:**
- Create: `backend/src/graphrag_ui/services/files.py`, `backend/src/graphrag_ui/api/files_routes.py`, `backend/tests/test_files.py`
- Modify: `backend/src/graphrag_ui/config.py` (+2 fields), `backend/src/graphrag_ui/main.py` (register routes), spec §8.3, `backend/pyproject.toml` (+`python-multipart`), `.env.example`.

**Interfaces:**
- Consumes: `_ws_path`, `get_project_role`, `can`/`Action.edit_content` — NOTE: if `Action` lacks an edit-content member, add `edit_content` to the enum mapping to `{owner, editor}` in `_PROJECT_ACTIONS` and to the permission test matrix (small domain change, include it here).
- Produces:
  - `services/files.py`:

```python
ALLOWED_EXTENSIONS: dict[str, set[str]] = {  # keyed by project.input_file_type
    "text": {".txt", ".md"}, "csv": {".csv"}, "json": {".json"},
}

class FileServiceError(Exception): ...      # routes map to 400
class FileTooLargeError(Exception): ...     # routes map to 413
class QuotaExceededError(Exception): ...    # routes map to 413

def _safe_name(project_input_file_type: str, filename: str) -> str
    # reject: empty, >255 chars, any of "/" "\\" "..", leading ".", ext not in whitelist → FileServiceError
def _dir_size(path: Path) -> int            # recursive byte size, 0 if missing
def quota_bytes() -> int                    # settings.project_quota_mb * 1MiB
def max_file_bytes() -> int                 # settings.upload_max_file_mb * 1MiB
async def save_file(project, filename: str, data: bytes) -> str   # returns stored name; overwrite same name allowed
    # checks: _safe_name, len(data) <= max_file_bytes, _dir_size(input)+_dir_size(output)+len(data) <= quota_bytes
    # writes via _ws_path(project.id)/"input"/name  (mkdir parents, tmp+replace atomic write)
async def list_files(project) -> list[dict] # [{name, size, modified_at}] sorted by name
async def delete_file(project, filename: str) -> None
```

  - API (all under existing auth; permission via `get_project_role` + `can`):
    - `POST /api/projects/{id}/files` multipart field `file` (editor+) → `201 {"name","size"}` | 400 invalid name/ext | 413 too large/quota
    - `GET /api/projects/{id}/files` (viewer+) → `{"files":[{name,size,modified_at}], "usage_bytes", "quota_bytes"}`
    - `DELETE /api/projects/{id}/files/{filename}` (editor+) → 204 (404 unknown)
  - audit actions: `file.uploaded`, `file.deleted` (payload: `{name, size}`)
  - `Settings`: `upload_max_file_mb: int = 50`, `project_quota_mb: int = 5000`

- [ ] **Step 1:** Write failing tests `backend/tests/test_files.py` (project fixture: create text project as alice via FakeInitializer override is NOT available here — real init runs in `client` fixture's project creation; simplest: create project through the API like Task 1 tests, real `graphrag init` ~seconds, mark the whole module's create helper `@pytest.mark.slow` only if runtime hurts — default keep real): upload `.md` to text project → 201 + appears in list; upload `.py` → 400; name `"../evil.txt"` → 400; upload 51 MiB bytes → 413 (use `b"x" * (51*1024*1024)` — memory OK); quota: set env `PROJECT_QUOTA_MB=1` via monkeypatch + cache_clear, upload 2nd file pushing usage >1 MiB → 413 (restore settings cache after); viewer DELETE → 403; delete → 204 + gone from list; usage_bytes reflects file sizes.

- [ ] **Step 2:** Confirm RED (404s), then implement service + routes. Route sketch (follow `projects_routes.py` conventions):

```python
@router.post("/{pid}/files")
async def upload_file(pid: uuid.UUID, file: UploadFile, db: DbSession, user: CurrentUser):
    project = await _project_or_404(db, pid)
    if not can(user.role, user.is_active, Action.edit_content, await get_project_role(db, pid, user.id)):
        raise _forbidden()
    data = await file.read()
    try:
        name = await files_service.save_file(project, file.filename or "", data)
    except FileServiceError as e:
        raise HTTPException(400, str(e))
    except (FileTooLargeError, QuotaExceededError) as e:
        raise HTTPException(413, str(e))
    await audit(db, user.id, "file.uploaded", "project", str(pid), {"name": name, "size": len(data)})
    await db.commit()
    return Response(status_code=201, content=json.dumps({"name": name, "size": len(data)}), media_type="application/json")
```

(`_project_or_404` exists in `projects_routes.py` — import or move to a shared spot in that module; do not duplicate the query.)

- [ ] **Step 3:** `uv add python-multipart` (required by FastAPI for multipart), wire `register_files_routes(app)` in `main.py`. Spec §8.3: append `UPLOAD_MAX_FILE_MB`、`PROJECT_QUOTA_MB` to the env-var list; `.env.example` gains both with comments.

- [ ] **Step 4:** Full backend suite + ruff green. Commit: `feat(api): project file upload with whitelist, quota, and path safety`.

---

### Task 3: Settings read/write with hash lock and version history

**Files:**
- Create: `backend/src/graphrag_ui/services/settings.py`, `backend/src/graphrag_ui/api/settings_routes.py`, `backend/tests/test_settings.py`, migration.
- Modify: `backend/src/graphrag_ui/adapters/models.py`, `main.py`.

**Interfaces:**
- Consumes: `_ws_path`, `audit`, `_project_or_404`, `can`.
- Produces:
  - Model `SettingsVersion(id: int pk autoincrement, project_id FK projects.id ondelete CASCADE index, content: Text, content_hash: String(64), saved_by: UUID FK users.id, created_at: DateTime tz server_default now)`; alembic migration `foundation_b settings versions`.
  - `services/settings.py`:

```python
import hashlib
class SettingsConflictError(Exception):
    def __init__(self, current_content: str, current_hash: str): ...

def read_settings(project) -> tuple[str, str]          # (content, sha256 of bytes on disk)
async def write_settings(session, project, content: str, expected_hash: str,
                         actor_id) -> str               # returns new hash; on mismatch raises
    # SettingsConflictError(current disk content+hash); happy path:
    # validate content parses as YAML (yaml.safe_load — ValueError on garbage),
    # atomic write (tmp+replace), insert SettingsVersion row, audit("settings.updated"), commit
async def list_versions(session, project) -> list[SettingsVersion]   # newest first, cap display at 50 rows returned
async def get_version(session, project, version_id: int) -> SettingsVersion | None
```

  - API:
    - `GET /api/projects/{id}/settings` (viewer+) → `{"content": str, "content_hash": str}`
    - `PUT /api/projects/{id}/settings` (editor+) body `{"content": str, "expected_hash": str}` → `200 {"content_hash"}` | `409 {"detail":"conflict","current_content":...,"current_hash":...}` | `400 invalid yaml` | `507` not used — quota on settings is not enforced (file is small)
    - `GET /api/projects/{id}/settings/versions` (viewer+) → `[{"id","content_hash","saved_by","created_at"}]`
    - `GET /api/projects/{id}/settings/versions/{vid}` (viewer+) → `{"id","content","content_hash","saved_by","created_at"}` (404 unknown)

- [ ] **Step 1:** Failing tests: GET returns content containing `input` and a 64-hex hash; PUT with correct hash → 200, disk content changed, a version row exists, `saved_by` = actor; PUT with stale hash → 409 + `current_content` equals what's on disk (simulate concurrent change by writing the file directly between GET and PUT via `_ws_path`); restore flow: write v2, fetch v1 content, PUT it back with fresh hash → disk equals v1 content, 3 version rows; PUT non-YAML `"not: [valid"` → 400; viewer PUT → 403; versions list newest-first.
- [ ] **Step 2:** RED → implement service/routes/migration (`uv run alembic revision --autogenerate -m "foundation_b settings versions"` + upgrade). Route maps `SettingsConflictError` → 409 JSON with `current_content`/`current_hash` (frontend diff flow depends on these exact keys).
- [ ] **Step 3:** Suite green. Commit: `feat(api): settings editor backend with hash optimistic lock and versions`.

---

### Task 4: `.env` per-key API (masked)

**Files:**
- Create: `backend/src/graphrag_ui/services/env_file.py`, `backend/src/graphrag_ui/api/env_routes.py`, `backend/tests/test_env.py`
- Modify: `main.py`.

**Interfaces:**
- Consumes: `_ws_path`, `audit`, `_project_or_404`, `can`.
- Produces:
  - `services/env_file.py`:

```python
_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")   # dotenv keys we manage
def _mask(value: str) -> str:
    return (value[:2] + "****") if len(value) >= 6 else "****"
def list_env(project) -> list[dict]           # [{key, masked}] from workspace .env (missing file → [])
def set_env_key(project, key: str, value: str) -> None
    # validate key against _KEY_RE (ValueError); upsert `key=value` line, preserve all other lines and order
def delete_env_key(project, key: str) -> None # remove line; missing key → KeyError (route → 404)
```

  - API: `GET /api/projects/{id}/env` (viewer+) → `{"keys":[{"key","masked"}]}`; `PATCH /api/projects/{id}/env` (editor+) body `{"key","value"}` → 204 (400 invalid key); `DELETE /api/projects/{id}/env/{key}` (editor+) → 204 (404 unknown). **Response bodies must never contain plaintext values** — including error payloads.
  - audit: `env.key_set`, `env.key_deleted` (payload `{key}` only).

- [ ] **Step 1:** Failing tests: PATCH `GRAPHRAG_API_KEY=sk-123456789` → 204; GET → `[{"key":"GRAPHRAG_API_KEY","masked":"sk****"}]` and `"sk-123456789"` absent from response text; disk `.env` contains the line; PATCH same key again replaces (single line, new value); DELETE → 204 + gone from list + line removed from disk; DELETE unknown → 404; key `bad-key` → 400; viewer PATCH → 403.
- [ ] **Step 2:** RED → implement → green. Commit: `feat(api): per-key project env management with masked reads`.

---

### Task 5: Dry-run validation endpoint

**Files:**
- Create: `backend/src/graphrag_ui/api/dry_run_routes.py`, `backend/tests/test_dry_run.py`
- Modify: `backend/src/graphrag_ui/adapters/workspace.py` (+`dry_run`), `main.py`.

**Interfaces:**
- Consumes: `_ws_path`, `_project_or_404`, `can` (editor+).
- Produces:
  - `adapters/workspace.py`:

```python
async def dry_run(root: Path) -> dict:
    """`graphrag index --root <root> --dry-run` via asyncio.to_thread, timeout 180s.
    Returns {"ok": bool, "output": str(stdout + stderr tail, last 20000 chars)}.
    TimeoutExpired → {"ok": False, "output": ... + "\\n[dry-run timed out after 180s]"}.
    FileNotFoundError (CLI missing) → WorkspaceInitError (route → 500)."""
```

  - API: `POST /api/projects/{id}/dry-run` (editor+) → `200 {"ok": bool, "output": str}`; the endpoint is synchronous (spec §6.1: 不進隊列); no audit.
- [ ] **Step 1:** Failing tests: (a) fast unit — monkeypatch `graphrag_ui.api.dry_run_routes.dry_run` (import it into the route module for overridability) to return canned dicts → assert route contract (200 + pass-through) and viewer 403; (b) `@pytest.mark.slow` real test — valid workspace → `ok is True` and `"Dry run complete" in output`; then corrupt `settings.yaml` (write `"{{{"` via `_ws_path`) → `ok is False`.
- [ ] **Step 2:** RED → implement → green (real dry-run ~5-10 s, offline). Commit: `feat(api): synchronous graphrag dry-run validation endpoint`.

---

### Task 6: Frontend Files tab

**Files:**
- Create: `frontend/src/components/FilesPanel.tsx`, `frontend/src/components/__tests__/FilesPanel.test.tsx`
- Modify: `frontend/src/api/client.ts` (FormData fix), `frontend/src/api/types.ts`, `frontend/src/pages/ProjectDetail.tsx` (enable `files` tab).

**Interfaces:**
- Consumes: Task 2 API; `api()`, `detailOf` pattern, `["projects", id]` keys.
- Produces:
  - `types.ts`: `export interface FileEntry { name: string; size: number; modified_at: string } export interface FilesOut { files: FileEntry[]; usage_bytes: number; quota_bytes: number }`
  - `client.ts` fix: set `Content-Type: application/json` only when `init.body` is a `string` (FormData must let the browser set the boundary). Keep every other behavior identical; existing tests must stay green.
  - `FilesPanel.tsx` (props: `{ projectId: string; inputFileType: "text"|"csv"|"json"; canEdit: boolean }`): AntD `Upload.Dragger` with `customRequest` posting `FormData` via `api()`; `accept` from a map mirroring backend whitelist (`.txt,.md` / `.csv` / `.json`); file `Table` (name / size KiB / modified / delete `Popconfirm`); quota `Progress` bar (`usage_bytes/quota_bytes`, red when >90%); errors via `message.error(detailOf(...))`.
  - `ProjectDetail.tsx`: `DISABLED_TABS` drops `files`; tab renders `<FilesPanel projectId={id} inputFileType={project.input_file_type} canEdit={canEditContent} />` where `canEditContent = admin || myRole owner/editor`.
- [ ] **Step 1:** Failing test (mirror `Projects.test.tsx` mock pattern): mock `api` returning `FilesOut` with two files; render `<MemoryRouter><FilesPanel projectId="p1" inputFileType="text" canEdit /></MemoryRouter>`; assert both names and the quota percent text render.
- [ ] **Step 2:** RED → implement → `npm test`/`tsc`/`build` green. Commit: `feat(frontend): files tab with upload, quota bar, and delete`.

---

### Task 7: Frontend Settings tab (dual-mode + conflict flow + versions + dry-run)

**Files:**
- Create: `frontend/src/components/SettingsPanel.tsx`, `frontend/src/components/__tests__/SettingsPanel.test.tsx`
- Modify: `frontend/src/api/types.ts`, `frontend/src/pages/ProjectDetail.tsx`.

**Interfaces:**
- Consumes: Task 3/4/5 APIs (`GET/PUT settings`, `GET versions[/{id}]`, `PATCH/DELETE env`, `POST dry-run`).
- Produces:
  - `types.ts`: `SettingsOut {content: string; content_hash: string}`, `SettingsVersionOut {id: number; content_hash: string; saved_by: string; created_at: string}`, `EnvKeyOut {key: string; masked: string}`.
  - `SettingsPanel.tsx` (props: `{ projectId: string; canEdit: boolean }`), three sections:
    1. **Editor** — `Radio.Group` mode toggle `YAML | Form`. YAML: monospace `TextArea` bound to content state. Form (`js-yaml` — add dep): fields for `completion_models.default_completion_model.{model, model_provider, auth_method}`, `embedding_models.default_embedding_model.{...}`, `chunking.{size, overlap}`, and read-only display of `input.{type, file_pattern}` (locked at creation, spec §6.5). Form edits apply to the parsed doc on save and re-serialize (`yaml.dump`). Save button (canEdit only) → `PUT {content, expected_hash: hashFromGet}`.
    2. **Conflict flow** — on 409, `Modal` shows `current_content` and your content side by side in two `<pre>` blocks with buttons `重新載入` (reload from response, discarding local) and `覆寫` (PUT again with `current_hash`). Buttons use these exact labels.
    3. **Versions** — collapsible list (`created_at`, `content_hash` first 8 chars); actions: 檢視 (`Modal` with content) and 還原 (fetch version content → PUT with current disk hash → on 409 fall into the conflict flow).
  - **Dry-run bar**: button `驗證設定 (dry-run)` (canEdit) → `POST /api/projects/{id}/dry-run` → inline `Alert` success/error showing `output` tail in a `<pre>`; **Env keys panel**: table `key | masked | (DELETE)` + `PATCH` form (key + `Input.Password` value, disabled when !canEdit).
- [ ] **Step 1:** Failing tests (mock `api`): YAML mode renders fetched content in the textarea; save invokes `api` with `PUT` and body `{content, expected_hash}`; a mocked 409 response opens the modal showing server content. (Do not test js-yaml round-trip internals; test observable behavior.)
- [ ] **Step 2:** `npm i js-yaml && npm i -D @types/js-yaml`. RED → implement → `npm test`/`tsc`/`build` green. Commit: `feat(frontend): dual-mode settings editor with conflict flow, versions, env keys, dry-run`.

---

### Task 8: Deploy sync, smoke, smell review, final review

**Files:**
- Modify: `.env.example`, `docker-compose.yml`, `deploy/helm/graphrag-ui/values.yaml` + `templates/api-deployment.yaml` (new env vars), README not present — skip; ledger/smell per below.

- [ ] **Step 1: Deploy sync** — add `UPLOAD_MAX_FILE_MB` and `PROJECT_QUOTA_MB` (with defaults as comments) to compose api environment and helm values → wired into the api deployment env. `docker compose config` + `helm lint` + both `helm template` renders green; assert the two names appear in rendered env.
- [ ] **Step 2: Full-stack smoke** (`docker compose up --build -d`, seed `.env`): login bootstrap admin → force password change → create text project → upload a `.md` file via UI → quota bar shows usage → Settings: edit chunk size in Form mode → save → YAML mode shows the change → make a second edit in a second browser context to force a 409 → conflict modal → 驗證設定 (dry-run) returns ok → env panel set `GRAPHRAG_API_KEY` → masked list shows `sk****` → delete uploaded file. `curl -sf localhost:8080/api/ready`. Tear down with `docker compose down`.
- [ ] **Step 3: All suites green** — backend `uv run pytest -v` + `ruff check`, frontend `npm test` + `npx tsc -b --noEmit` + `npm run build`.
- [ ] **Step 4: Smell review (spec §9)** — greps must be empty: `grep -rn "import graphrag\|from graphrag" backend/src/graphrag_ui/{domain,services}`, `grep -rn "fastapi\|HTTPException" backend/src/graphrag_ui/services`, `grep -rn "sqlalchemy" backend/src/graphrag_ui/domain`; no source file >400 lines; **new comments are English** (grep for CJK in files touched this phase: `git diff --name-only 24ed20c..HEAD | xargs grep -lnP '[\x{4e00}-\x{9fff}]' backend/src frontend/src | grep -v __tests__` — hits are zh-TW legacy in untouched sections only). Fix findings, re-run tests.
- [ ] **Step 5:** Commit: `chore: foundation-b deploy sync and smell review`. Then the controller runs the whole-branch final review (per subagent-driven-development).

---

## Self-Review

- **Spec coverage:** §6.1 files/settings/env/dry-run endpoints (Tasks 2-5), §6.2 dual-mode + hash lock + versions + dry-run validation (Tasks 3, 7), §6.5 whitelist per `input_file_type` + form locked input display (Tasks 2, 7), §8.3 new env names (Task 2 spec edit, Task 8 wiring), §10 quotas/path traversal/never-plaintext (Tasks 2-4), §5 single-owner amendment + Foundation-A carryover tests (Task 1). Not in scope: indexing/jobs (Phase 3), gzip/code-split (deferred to Query/Explore).
- **Placeholder scan:** none — every step carries exact names, payloads, and status codes.
- **Type consistency:** `Action.edit_content` introduced in Task 2 with matrix update; FilesOut/SettingsOut/version schemas match between Tasks 2-5 and 6-7; conflict payload keys `current_content`/`current_hash` identical in Task 3 backend and Task 7 frontend.
