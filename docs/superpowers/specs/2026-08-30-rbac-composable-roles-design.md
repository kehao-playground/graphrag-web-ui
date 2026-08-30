# Composable Roles & Permission Atoms (RBAC v2) — Design

Date: 2026-08-30
Status: approved in chat 2026-08-30; pending implementation plan.

User decisions (2026-08-30, chat):

1. **Personas**: the console serves developers, system administrators, and
   document maintainers. The doc maintainer's project scope is **content +
   self-service re-indexing** — they upload/delete input documents and run
   indexing jobs, but never touch `settings.yaml` or env API keys.
2. **Project creation stays open** to every active user. Persona separation
   lives at the project-member layer, not in a global "developer" role.
3. **User management and system operations split** into two independent
   global roles. The current `admin` becomes their composition.
4. **Roles are DB entities and composable**: a user holds multiple global
   roles; effective permissions are the union. Admins can create custom
   roles; seeded built-in roles cover most scenarios.
5. **Custom roles are supported at both scopes**: global (assigned to
   users) and project (assigned to members).
6. Two approved **semantic changes**: (a) project rename moves from editor
   to `project:manage` (owner + ops); (b) env API key management moves from
   content editing to `project:edit_settings`.

## 1. Problem

The current model is two-tier and rigid:

- Global: `users.role` ∈ {`admin`, `user`}. `admin` is a monolith that
  bundles user management with cross-project operational power
  (`can()`'s admin bypass: see-all-projects + act-as-owner anywhere).
- Project: `project_members.role` ∈ {`owner`, `editor`, `viewer`} as plain
  strings.

Three gaps:

1. **Doc maintainer gap** — between `viewer` (no uploads) and `editor`
   (full settings.yaml + LLM API key control). Maintainers who only curate
   documents must not hold key management.
2. **Admin monolith** — "manage users" and "operate the system" are
   different jobs (helpdesk vs. ops); today they cannot be delegated
   separately.
3. **Fixed catalogs** — both tiers are hard-coded strings; no way to
   express e.g. "read-only auditor who can see job history".

## 2. Goals

- Permission **atoms** as the currency; roles = sets of atoms; effective
  permissions = union over all roles a principal holds.
- Built-in seeded roles (immutable): global `user_admin`, `ops`; project
  `viewer`, `maintainer`, `editor`, `owner`.
- Custom roles (both scopes) created/edited/deleted by holders of
  `users:manage`; deletion blocked while in use.
- Current `admin` users migrate to `user_admin` + `ops` with identical
  effective permissions.
- Frontend switches from `user.role === "admin"` / member-role string
  checks to **permission atoms** delivered by the backend
  (`UserOut.permissions`, `ProjectOut.my_permissions`).
- Proxy mode (`AUTH_MODE=proxy`): `PROXY_ADMIN_EMAILS` grants the
  `user_admin` + `ops` composition; JIT provisioning and
  authoritative-upward reconciliation semantics preserved.
- Local mode bootstrap admin gets `user_admin` + `ops`.

## 3. Non-goals

- Role inheritance / hierarchies between custom roles (composition is
  union only).
- Per-project role catalogs — the `roles` table is a global catalog; a
  custom project role is assignable in every project.
- IdP group → role mapping in proxy mode (`PROXY_ADMIN_EMAILS` remains
  the only external mapping).
- Custom atoms — the atom catalog is fixed in code; custom roles are
  subsets of it.
- Row-level or content-level permissions (document-level ACLs).
- Audit-log viewing UI (audit rows are written, as today).

## 4. Permission model

### 4.1 Atoms

Global atoms (held via global roles):

| Atom | Grants |
|---|---|
| `users:manage` | User CRUD, password resets, **role assignment, custom role CRUD** |
| `projects:view_any` | See every project in listings |
| `projects:act_any` | Perform every project-level action in every project |
| `projects:create` | Create projects — **baseline for every active user, a domain constant, never stored in DB** |

Project atoms (held via project member roles):

| Atom | Grants |
|---|---|
| `project:view` | View project, file list, job list/logs/SSE, query, explore, settings read + versions, env key names |
| `project:edit_content` | Upload/delete input documents |
| `project:run_jobs` | Trigger/cancel indexing jobs, preflight |
| `project:edit_settings` | Write `settings.yaml`, dry-run, **set/delete env API keys** |
| `project:manage` | Rename project, delete project, manage members |

Implication rules (resolution-time, in `domain/permissions.py`):

- `projects:act_any` ⇒ every project atom in every project.
- `projects:view_any` ⇒ `project:view` in every project.

Baseline for every **active** user: `projects:create`, own profile and
password management (not permission-gated, as today).

### 4.2 Built-in roles (seeded, `is_system = true`, immutable)

| Scope | Role | Atoms |
|---|---|---|
| global | `user_admin` | `users:manage` |
| global | `ops` | `projects:view_any`, `projects:act_any` |
| project | `viewer` | `project:view` |
| project | `maintainer` | `project:view`, `project:edit_content`, `project:run_jobs` |
| project | `editor` | `project:view`, `project:edit_content`, `project:run_jobs`, `project:edit_settings` |
| project | `owner` | all five project atoms |

Current-vs-new effective permissions:

- old `admin` ≡ `user_admin` + `ops` (identical).
- old `viewer` ≡ new `viewer`.
- old `editor` = new `editor` **minus** project rename (semantic change a)
  **minus** env key management (semantic change b).
- new `maintainer` = old `editor` minus rename minus settings/env keys.

Built-in role ids are fixed literal UUIDs (constants in code; migration
seeds by id, `ON CONFLICT (id) DO NOTHING`):

```
user_admin  00000000-0000-4000-8000-000000000001
ops         00000000-0000-4000-8000-000000000002
viewer      00000000-0000-4000-8000-000000000003
maintainer  00000000-0000-4000-8000-000000000004
editor      00000000-0000-4000-8000-000000000005
owner       00000000-0000-4000-8000-000000000006
```

### 4.3 Route → atom mapping (backend)

| Route group | Old check | New atom |
|---|---|---|
| `/api/admin/users/*` (all) | `require_admin` | `users:manage` |
| Project list (all vs. mine) | `user.role != "admin"` branch | `projects:view_any` |
| Project rename / delete / members PUT+DELETE | `update_project` / `delete_project` / `manage_members` | `project:manage` |
| Project GET, members GET, files GET, jobs GET+logs SSE, query + stream, explore, settings GET + versions, env keys GET | `view_project` | `project:view` |
| Files upload/delete | `edit_content` | `project:edit_content` |
| Jobs trigger/cancel, preflight | `edit_content` | `project:run_jobs` |
| Settings PUT, dry-run, env keys set/delete | `edit_content` | `project:edit_settings` |
| Project create | `create_project` | baseline (`projects:create`) |

Preflight intentionally follows `project:run_jobs`: it exists to serve
the trigger dialog.

## 5. Data model & migration

### 5.1 Schema

```
roles             id UUID PK (built-ins: fixed literals above)
                  scope VARCHAR CHECK (scope IN ('global','project'))
                  name VARCHAR(50)  -- unique per (scope, name)
                  description VARCHAR(200) NOT NULL DEFAULT ''
                  permissions TEXT[] NOT NULL DEFAULT '{}'
                  is_system BOOLEAN NOT NULL DEFAULT false
                  created_at TIMESTAMPTZ NOT NULL DEFAULT now()

user_roles        user_id UUID FK users(id) ON DELETE CASCADE
                  role_id UUID FK roles(id)  ON DELETE RESTRICT
                  PK (user_id, role_id)

project_members   (project_id, user_id) PK  -- unchanged
                  role_id UUID NOT NULL FK roles(id) ON DELETE RESTRICT  -- replaces role VARCHAR

users             role column DROPPED
```

Scope constraints (`user_roles.role_id` must be global-scope;
`project_members.role_id` must be project-scope) are enforced in the
service layer; Postgres CHECK constraints cannot span tables without
triggers, which we do not add.

### 5.2 Migration (one alembic revision, order matters)

1. Create `roles`; seed the six built-ins by fixed id.
2. Create `user_roles`.
3. Backfill: for every `users.role = 'admin'` row, insert
   `(user_id, user_admin)` and `(user_id, ops)`. `role = 'user'` rows get
   nothing (baseline is a code constant).
4. Add `project_members.role_id` as nullable UUID; backfill
   `owner`/`editor`/`viewer` → fixed ids; then set `NOT NULL` + FK.
5. Drop `users.role` and `project_members.role`.

The revision is written to be safe on an empty database (seed inserts
are `ON CONFLICT DO NOTHING`; backfills are no-ops).

### 5.3 Data integrity rules (service layer)

- `set_member`/`put_member`: `role_id` must reference a project-scope
  role; granting the built-in `owner` id is rejected
  (`400 member_owner_protected`, single-owner policy unchanged — the
  owner row is still created at project creation and 400-protected).
- `user_roles` writes: role must be global-scope.
- Custom role create/update: `permissions` must be a subset of the atom
  catalog **for that scope** (global roles may not carry project atoms
  and vice versa); `name` unique per scope; `is_system` not settable.
- Custom role delete: `409 role_in_use` if any `user_roles` or
  `project_members` row references it (service check first; RESTRICT is
  the backstop, not the UX).
- System role update/delete: `400 role_is_system`.

## 6. Backend changes

### 6.1 Permission resolution

- `domain/permissions.py` rewritten around atoms:
  `can(global_perms: frozenset[str], is_active: bool, action: Atom,
  member_perms: frozenset[str] | None) -> bool`. Pure, no I/O, no
  external imports (layer rule). Implication rules of §4.1 live here.
  `Action` enum is replaced by the atom enum (values identical to atom
  strings); route files migrate mechanically (§4.3).
- The auth dependency (`get_current_user` / `resolve_proxy_user`) loads
  the user's effective global atoms once per request —
  `SELECT roles.permissions FROM user_roles JOIN roles …` alongside the
  user fetch — and exposes them on the request-scoped principal.
  `require_admin` becomes `require_atom("users:manage")`.
- Project routes: `get_project_role(db, pid, user.id)` becomes
  `get_member_perms(db, pid, user.id)` returning the member role's
  atom set (`None` when not a member); `can()` applies the
  `act_any`/`view_any` implications.

### 6.2 Last-user-manager protection (generalized from last-admin)

Invariant: **at least one active user with effective `users:manage`**
must exist at all times. Enforced over the post-change state for every
mutation that can affect it:

- patch user roles / `is_active` (user mutations),
- delete a custom role (all its holders lose it),
- edit a custom role's permission set (may drop `users:manage`).

Violation → `400 last_user_manager_protected`. Custom-role paths must
evaluate holders of the edited/deleted role, not role names — a user can
hold `users:manage` only through a custom role. Self-guard generalizes
symmetrically: an effective `users:manage` holder cannot change their own
global roles or active status (`400 user_self_change_forbidden`, kept).

### 6.3 Auth flows

- **Local bootstrap** (`bootstrap_admin`): creates the bootstrap admin
  with grants `[user_admin, ops]`. Existing-behavior warning unchanged.
- **Proxy JIT + reconciliation** (`get_or_provision_user`):
  provisioning grants `[user_admin, ops]` when the email is in
  `PROXY_ADMIN_EMAILS`; the authoritative-upward pass adds **missing**
  grants (not just role-name equality) with audit
  `user.roles_promoted`, payload `{"via": "proxy_admin_emails"}`.
  Case-insensitive matching and the `PROXY_AUTH_SECRET` trust anchor are
  unchanged.
- **JWT**: the `role` claim is dropped; `roles: [name, …]` is added for
  display. Authorization is already DB-driven per request
  (`resolve_access_user` loads the `User` row from `sub`) and stays so;
  role changes take effect on the next request, no token wait.

### 6.4 Audit

New actions: `role.created`, `role.updated`, `role.deleted`,
`user.roles_changed` (payload: added/removed role ids). Existing
`member.role_changed` / `member.added` payloads switch from role strings
to role ids + names. `user.role_promoted` is renamed `user.roles_promoted`.

## 7. API contract changes (openapi.json + types regenerated)

- `UserOut`: `role` removed; `roles: [RoleOut]`, `permissions: [str]`
  (effective global atoms) added.
- `UserCreateIn` / `UserUpdateIn`: `role` → `roles: [UUID]` (global-scope
  role ids).
- `UserBriefOut`: unchanged (deliberately excludes admin fields).
- `MemberOut`: `role: str` → `role_id: UUID`, `role_name: str`.
- `MemberIn`: `role: "editor"|"viewer"` → `role_id: UUID` (project-scope,
  non-owner).
- `ProjectOut`: adds `my_permissions: [str]` — the caller's effective
  project atoms (including `act_any`/`view_any` implications) for that
  project; also present in `GET /api/projects` list items.
- New `RoleOut`: `{id, scope, name, description, permissions, is_system}`.
- New endpoints:
  - `GET /api/roles` — any authenticated active user; optional
    `?scope=global|project` filter; serves the member picker and admin
    pages. Custom role names are visible team-wide; acceptable for an
    internal tool.
  - `/api/admin/roles` (`users:manage`): `GET` (with usage counts),
    `POST`, `PATCH /{id}` (custom only), `DELETE /{id}` (custom only,
    `409 role_in_use`).
- Error codes added: `last_user_manager_protected`, `role_is_system`,
  `role_in_use`, `role_scope_mismatch`, `role_not_found`,
  `role_permissions_invalid`. `user_last_admin_protected` is removed
  (superseded).

## 8. Frontend

- **Auth store / `Layout`**: user shape carries `roles` +
  `permissions`; the Admin Users nav item renders on
  `permissions.includes("users:manage")`; a new Admin Roles entry
  (`/admin/roles`) under the same condition.
- **AdminUsers**: role single-select → **global-role multi-select**
  (ids, from `GET /api/roles?scope=global`); self-row role editing
  locked (backend enforces).
- **New AdminRoles page**: table grouped by scope; create/edit modal
  with atom checkboxes (grouped, scope-filtered); system roles locked
  (view only); delete handles `409 role_in_use`.
- **ProjectDetail / Projects**: every action button switches to
  `my_permissions` atoms — upload/delete files → `project:edit_content`;
  trigger/cancel jobs + preflight → `project:run_jobs`; settings editor,
  dry-run, env key set/delete → `project:edit_settings`; members,
  rename, delete project → `project:manage`. Member role `Select`
  options come from `GET /api/roles?scope=project` minus the built-in
  `owner` (owner not grantable; owner row locked, as today).
- **i18n**: zh-TW + en-US strings for new pages, atoms, and role names
  (`roles.user_admin` … `roles.owner`, `perms.usersManage` …).
- `types.generated.ts` regenerated via `npm run gen:types`.

## 9. Testing

- **Domain unit tests**: union resolution, implication rules
  (`act_any`/`view_any`), baseline `projects:create`, `is_active`
  short-circuit, scope isolation (global atoms never satisfy project
  checks and vice versa).
- **Route tests** (extend existing suites):
  - `maintainer` full path: files + jobs + preflight allowed; settings
    PUT, dry-run, env key set/delete, rename, members → 403.
  - `ops`-only user: sees all projects, owner-level actions everywhere,
    `403` on `/api/admin/users`.
  - `user_admin`-only user: user CRUD allowed, no project visibility
    beyond own memberships.
  - Custom global role (e.g. `auditor` = `projects:view_any`): sees all
    projects, every write 403.
- **Guard tests**: last-user-manager protection across all four mutation
    classes (incl. a holder whose only source of `users:manage` is a
    custom role, and a custom-role permission edit that drops the atom);
    self-change rejection.
- **Role CRUD tests**: scope validation, atom-subset validation,
  `role_is_system`, `role_in_use`, unique-name-per-scope.
- **Migration test**: legacy fixtures (`users.role='admin'/'user'`,
  member strings) → expected grants and `role_id`s; empty-database run.
- **Proxy-mode tests**: JIT grants the composition; reconciliation adds
  missing grants; demote-from-list behavior documented as today.
- **Frontend tests**: role multi-select in AdminUsers, AdminRoles page
  (create/lock/delete-409), ProjectDetail buttons driven by
  `my_permissions` (maintainer fixture), member select from role
  catalog, Layout nav gating.
- **Full gates**: `uv run pytest -m "not slow"`, `uv run ruff check`,
  `npm test`, `npx tsc -b --noEmit`, `npm run build`, openapi +
  generated-types diff in CI.

## 10. Compatibility & risks

- **Breaking API change** (`role` → `roles`/`permissions`,
  `MemberIn.role` → `role_id`). Single-deployment product with no
  external API consumers; frontend and backend ship together. The
  OpenAPI diff in CI forces the pair to stay in sync.
- **Maintainer can spend indexing budget** (semantic change accepted in
  decision 1): bounded by `MAX_CONCURRENT_JOBS`, per-project quota,
  disk watermark, and query rate limits — all unchanged.
- **Editor loses rename + env keys** (semantic changes a/b, approved).
  Migration cannot silently re-grant; teams that relied on the old
  semantics assign the `editor` role as before and accept the split. To
  restore key/settings control without member management, grant a custom
  role containing `project:edit_settings`; rename comes only with
  `project:manage` (which also carries member management and deletion).
- **One extra roles query per request**: negligible at 10–50 users;
  fold into the user-fetch JOIN if ever measured.
- **Role catalog visible to all users** (`GET /api/roles`): names and
  atom sets leak nothing sensitive; keeps the member picker simple.
