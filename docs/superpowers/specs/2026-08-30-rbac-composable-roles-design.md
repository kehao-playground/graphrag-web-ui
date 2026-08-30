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
6. Two approved **semantic changes**: (a) `PATCH /api/projects/{id}` —
   rename *and* description, one route, one check — moves from editor to
   `project:manage` (owner + ops); (b) env API key management moves from
   content editing to `project:edit_settings`. Nothing else changes
   scope: in particular the jobs preflight probe keeps its current
   `project:view` gate (§4.3).

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
| `project:view` | View project, file list, job list + preflight + logs/SSE, query, explore, settings read + versions, env key names |
| `project:edit_content` | Upload/delete input documents |
| `project:run_jobs` | Trigger/cancel indexing jobs |
| `project:edit_settings` | Write `settings.yaml`, dry-run, **set/delete env API keys** |
| `project:manage` | Edit project name/description, delete project, manage members |

Implication rules (resolution-time, in `domain/permissions.py`):

- `projects:act_any` ⇒ every project atom in every project, and ⇒
  `projects:view_any`. The second half is not redundant: the project-list
  branch reads `projects:view_any` only, so without it a custom role
  holding just `act_any` could act on every project while seeing none of
  them in the listing.
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
- old `editor` = new `editor` **minus** project PATCH (rename *and*
  description — semantic change a) **minus** env key management
  (semantic change b).
- new `maintainer` = old `editor` minus project PATCH minus
  settings/env keys.

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
| Project PATCH (rename **and** description) / delete / members PUT+DELETE | `update_project` / `delete_project` / `manage_members` | `project:manage` |
| Project GET, members GET, files GET, jobs GET + preflight + logs SSE, query + stream, explore, settings GET + versions, env keys GET | `view_project` | `project:view` |
| Files upload/delete | `edit_content` | `project:edit_content` |
| Jobs trigger/cancel | `edit_content` | `project:run_jobs` |
| Settings PUT, dry-run, env keys set/delete | `edit_content` | `project:edit_settings` |
| Project create | `create_project` | baseline (`projects:create`) |

Two clarifications on this table:

- **`PATCH /api/projects/{id}` moves as a whole.** One route writes both
  `name` and `description` behind a single `Action.update_project` check
  (`api/projects_routes.py` → `patch_one`), so semantic change (a) takes
  description editing with it: an `editor` loses both. Splitting the
  check per field is deliberately not done — one route, one atom.
- **Preflight keeps `project:view`.** That is its check today
  (`api/jobs_routes.py` → `preflight` uses `Action.view_project`, not
  `edit_content`), it is a read-only status probe, and `JobsPanel` fires
  it unconditionally on mount with an error toast on failure — gating it
  on `project:run_jobs` would greet every viewer with a red toast. No
  third semantic change.

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

`permissions` is a Postgres `TEXT[]`, not the JSONB used elsewhere in
this schema: the value is a flat set of short atom strings, and array
containment (`permissions @> ARRAY['users:manage']`) keeps "every role
granting `users:manage`" a plain, indexable predicate — the
last-user-manager guard (§6.2) runs exactly that on every user
mutation. SQLAlchemy maps it as `ARRAY(Text)`.

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

**Downgrade** is lossy and says so rather than guessing. It re-adds
`users.role` and `project_members.role`, maps holders of `user_admin`
**or** `ops` back to `'admin'` and everyone else to `'user'`, maps the
four built-in project roles back to their strings, and floors **custom**
project roles at `'viewer'` — never silently upgrading a member's power
on the way down. Custom roles themselves vanish with the tables; the
revision docstring states the loss.

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
- **Principal shape.** `get_current_user` / `sse_user_from_request` stop
  returning the bare `User` ORM row and return a frozen dataclass
  `Principal(user: User, global_perms: frozenset[str])` defined in
  `api/deps.py`; the `CurrentUser` / `SseUser` aliases point at it and
  `p.user` still carries the ORM row for `actor_id`, email and
  `is_active`. The dataclass stays in the API layer: `domain.can()` keeps
  taking plain frozensets and never sees an ORM object (layer rule). Both
  `get_current_user` and `resolve_proxy_user` load the atoms once per
  request — `SELECT roles.permissions FROM user_roles JOIN roles …`
  alongside the user fetch. `require_admin` becomes
  `require_atom("users:manage")`.
- **Service signatures that read `users.role` today change with it**:
  `services.projects.create_project` (baseline check on the creator),
  `services.projects.list_projects` (branches on `projects:view_any`
  instead of `user.role != "admin"`), and
  `services.users.patch_user_guarded` (self-guard + last-user-manager)
  all take the actor's atom set instead of the ORM row's role. Those
  three plus `services/auth.py` (§6.3) are the complete set of non-API
  `.role` readers.
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
  with grants `[user_admin, ops]`. The "an admin already exists" probe
  changes from `select(User).where(User.role == 'admin')` +
  `scalar_one_or_none()` to an `EXISTS` / `LIMIT 1` query over
  `user_roles JOIN roles` for any active holder of `users:manage`. The
  limit is not cosmetic: the current call raises `MultipleResultsFound`
  — a startup crash — as soon as a second admin exists, and `user_admin`
  is expected to have several holders. The ignored-password warning keeps
  its wording and now names the first such holder.
- **Proxy JIT + reconciliation** (`get_or_provision_user`):
  provisioning grants `[user_admin, ops]` when the email is in
  `PROXY_ADMIN_EMAILS`; the authoritative-upward pass adds **missing**
  grants (grant-set difference, not role-name equality) and keeps the
  existing audit action `user.role_promoted` with its
  `{"via": "proxy_admin_emails"}` payload — renaming it would split
  historical audit queries for no gain.
  Case-insensitive matching and the `PROXY_AUTH_SECRET` trust anchor are
  unchanged.
- **JWT**: the `role` claim is dropped and **nothing replaces it**.
  Nothing reads it today (the SPA takes its user shape from the
  `/api/auth/login` and `/api/auth/me` `UserOut`), and a display-only
  `roles` claim would cost a name lookup at issue time while drifting
  from the DB. Authorization is already DB-driven per request
  (`resolve_access_user` loads the `User` row from `sub`) and stays so;
  role changes take effect on the next request, no token wait.

### 6.4 Audit

New actions: `role.created`, `role.updated`, `role.deleted`,
`user.roles_changed` (payload: added/removed role ids). Existing
`member.role_changed` / `member.added` payloads switch from role strings
to role ids + names. `user.role_promoted` and `user.updated` keep their
action names — only the latter's payload carries `roles` where it used
to carry `role`.

## 7. API contract changes (openapi.json + types regenerated)

- `UserOut`: `role` removed; `roles: [RoleOut]`, `permissions: [str]`
  (effective global atoms) added.
- `UserUpdateIn`: `role: "admin"|"user"` → `roles: [UUID] | None`
  (global-scope role ids; omitted = no change, `[]` = strip every global
  role).
- `UserCreateIn`: **gains** `roles: [UUID] = []`. This is an addition,
  not a rename — the model has no `role` field today and `create_user()`
  takes no role, so a newly created user is always a plain user that
  must be PATCHed afterwards. Accepting grants at creation keeps the
  admin create modal a single request; `create_user()` gains the
  parameter and applies the same global-scope validation as the PATCH
  path.
- `UserBriefOut`: unchanged (deliberately excludes admin fields).
- `MemberOut`: `role: str` → `role_id: UUID`, `role_name: str`.
- `MemberIn`: `role: "editor"|"viewer"` → `role_id: UUID` (project-scope,
  non-owner).
- `ProjectOut`: adds `my_permissions: [str]` — the caller's effective
  project atoms (including `act_any`/`view_any` implications) for that
  project; also present in `GET /api/projects` list items. The list route
  resolves them in **one** query (`project_members JOIN roles`, filtered
  by the caller and the listed project ids, folded into a
  `{project_id: atoms}` map before serialization) — never one
  `get_member_perms` per row.
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
  (superseded). `require_atom` **keeps the existing `admin_only` code**
  on the `/api/admin/*` routers, and project-atom failures keep
  `forbidden`: both are already in the i18n error catalog and pinned by
  `tests/test_error_codes.py`, so only the `admin_only` message strings
  are reworded away from "admin" toward "requires user management".

## 8. Frontend

- **Auth store / `Layout`**: user shape carries `roles` +
  `permissions`; the Admin Users nav item renders on
  `permissions.includes("users:manage")`; a new Admin Roles entry
  (`/admin/roles`) under the same condition.
- **AdminUsers**: role single-select → **global-role multi-select**
  (ids, from `GET /api/roles?scope=global`), in both the create modal
  (`UserCreateIn.roles`) and the row editor; self-row role editing
  locked (backend enforces).
- **New AdminRoles page**: table grouped by scope; create/edit modal
  with atom checkboxes (grouped, scope-filtered); system roles locked
  (view only); delete handles `409 role_in_use`. The `project:manage`
  checkbox carries an inline warning that it grants project deletion and
  member management to any member holding the role (§10).
- **ProjectDetail / Projects**: every action button switches to
  `my_permissions` atoms — upload/delete files → `project:edit_content`;
  trigger/cancel jobs → `project:run_jobs` (the preflight query stays
  ungated: it is a `project:view` read); settings editor,
  dry-run, env key set/delete → `project:edit_settings`; members,
  project name/description edit, delete project → `project:manage`. Member role `Select`
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
    PUT, dry-run, env key set/delete, project PATCH (both `name` and
    `description`), members → 403.
  - `viewer` regression: preflight still 200 — it did **not** move to
    `project:run_jobs` — while every write stays 403.
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
  member strings) → expected grants and `role_id`s; a user who is both a
  global `admin` and a project `editor`, so the two backfills are shown
  not to interfere; empty-database run; downgrade re-derives the legacy
  strings and floors custom project roles at `viewer`.
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
- **Editor loses project PATCH + env keys** (semantic changes a/b,
  approved). Because name and description share one route, description
  editing moves with the rename. Migration cannot silently re-grant;
  teams that relied on the old semantics assign the `editor` role as
  before and accept the split. To restore key/settings control without
  member management, grant a custom role containing
  `project:edit_settings`; project PATCH comes only with
  `project:manage` (which also carries member management and deletion).
- **Custom project roles may carry `project:manage`**, so "one owner" no
  longer implies "one manager": a member holding such a role can rename,
  delete the project and manage members without being
  `projects.owner_id`. Accepted — that is the point of composable roles
  — and the built-in `owner` id stays non-grantable (§5.3). The
  AdminRoles form warns inline when the atom is selected.
- **`admin_only` outlives the `admin` role**: the error code survives for
  contract stability while the concept it names is gone. The reworded
  i18n strings carry the meaning; renaming the code is not worth the
  frontend and test churn.
- **One extra roles query per request**: negligible at 10–50 users;
  fold into the user-fetch JOIN if ever measured.
- **Role catalog visible to all users** (`GET /api/roles`): names and
  atom sets leak nothing sensitive; keeps the member picker simple.

## 11. Documentation & assets

Shipped in the same PR as the code (AGENTS.md: a README change updates
the zh-TW mirror in the same PR).

- `README.md` — the admin-capabilities paragraph (~L125), the
  `PROXY_ADMIN_EMAILS` "held at `role=admin`" line (~L225) and the proxy
  sequence-diagram note (~L243) restate the model as roles + atoms.
- `docs/zh-TW/README.md` — mirrored in the same PR.
- `docs/oauth2-proxy.md` and `docs/zh-TW/oauth2-proxy.md` — the
  promote-on-every-request paragraph (zh-TW ~L93) becomes "the listed
  email is re-granted the `user_admin` + `ops` composition on every
  request; remove it from the variable before revoking the grants in
  AdminUsers".
- `deploy/helm/graphrag-ui/values.yaml` (~L86, the
  `held at role=admin (spec §5.2)` comment) and
  `deploy/helm/graphrag-ui/templates/NOTES.txt` (~L46, break-glass
  wording).
- Screenshots: retake `docs/assets/screenshots/{en,zh}/admin-users.png`
  with the role multi-select, and add `admin-roles.png` for the new page,
  referenced from both READMEs.
- No `AGENTS.md` / `CONTRIBUTING.md` change — no new commands or gates.
