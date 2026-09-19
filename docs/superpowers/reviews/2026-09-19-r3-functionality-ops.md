# R3 — Functionality & operations review (2026-09-19)

## Intro

**Scope.** Every spec in `docs/superpowers/specs/` (the 2026-08-19 design, hygiene, i18n,
oauth2-proxy, RBAC and knowledge-manager specs — 3,892 lines) read in full and judged section by
section against the shipped code: `backend/src/graphrag_ui/` (`main.py`, `config.py`, every
`api/*_routes.py`, `services/{jobs,runner_loop,retention,health,settings,files,test_runs,
index_snapshots,auth}.py`, `adapters/{jobs_repo,index_runner,models}.py`, `domain/jobs.py`),
`frontend/src/` (`App.tsx`, `pages/*`, `components/*`, `components/{project,files,tests}/*`,
`api/client.ts`, `api/types.ts`, both locale catalogs), the deploy surface (`docker-compose.yml`,
`docker-compose.proxy-auth.yml`, `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf`,
every Helm template and `values.yaml`, `NOTES.txt`, `.env.example`, `.github/workflows/ci.yml`),
`README.md`, `docs/zh-TW/README.md`, `docs/oauth2-proxy.md`, `CHANGELOG.md` and the committed
`openapi.json`. R1/R2 findings were the exclusion list: anything already filed is referenced by id,
never re-filed; the R1 and R2 "R3 hand-offs" are dispositioned in their own section.

**Tools run.** A clean-clone README quickstart against a real compose stack (`git clone` of
`main@3fa8d3a` into the session scratchpad, `cp .env.example .env`, `docker compose -p r3quick up
--build -d`, then every step of the README's ten through the API, including **one real
`standard` index of a two-file corpus with the key in `~/.graphrag-p3.key`** — authorised in-session —
and the four query modes, explore tables and graph); `docker compose config`; `helm lint` and
`helm template` with five value sets; `docker manifest inspect` for the chart's default images;
`openapi.json` regenerated in the AGENTS.md Docker recipe and diffed; `psql`/`duckdb` inside the
stack to inspect snapshot rows and `documents.parquet`; `grep`/`git log -S` for history.

**Host.** Intel Mac (`x86_64`), Docker 29.4.0, Compose v5.1.2, Helm v4.2.4, Node v24.18.0. Backend
tooling only inside the `ghcr.io/astral-sh/uv:python3.12-bookworm` recipe.

**No code was changed in this phase.** `git status --short` at the end shows only this file, the
plan's §8 row and the pre-existing untracked `.codegraph/`. The regenerated `openapi.json` was
byte-identical to the committed copy. The quickstart clone, its compose project (`r3quick`) and
its volumes were removed after the run (`docker compose -p r3quick down -v`).

### Commands and exit status

| command | exit | result |
|---|---|---|
| `git clone /Users/kehao/projects/graphrag-web-ui quickstart && cp .env.example .env` + set `JWT_SECRET`/`BOOTSTRAP_ADMIN_*` | 0 | `docker compose config --quiet` OK |
| `docker compose -p r3quick up --build -d` (cold build, one cached layer) | 0 | 1 m 54 s wall; `api` ran 10 alembic revisions and started; `/api/health` 200, `/api/ready` `{"db":"ok","graphrag":"3.1.2","disk_ok":true}` |
| README steps 4–7 via curl (login → 403 on `/api/projects` → change-password 204 → 200; create project 201 in 0.9 s; upload `small.txt` 201; `wrong.csv` 400 `file_ext_not_allowed`; env key PATCH 204, read-back `sk****`; dry-run 200 in 6.5 s) | 0 | all as documented |
| upload of a 2.2 MB `.txt` through the stack | — | **413 `text/html` from nginx** (R2-04 reproduced in the quickstart; README step 6 says the cap is `UPLOAD_MAX_FILE_MB`) |
| step 8 with a fake key: `POST /jobs` 201 → second `POST /jobs` 409 `job_conflict` → upload during job 409 `project_indexing` → job `failed`, exit 1, error = litellm `AuthenticationError` tail | — | freeze and mutex hold; **api container log shows nothing about the job** (R3-11) |
| step 8 with the real key, `standard`, 2 files (78 B + 315 B) | — | `succeeded` in 50 s, `stats.num_documents = 2`; **files list: both `skipped`, health `skipped: 2, indexed: 0`** (R3-01) |
| step 9: `POST /query` local 6.5 s, global 3.1 s, drift 66.5 s, basic 2.6 s; SSE stream `basic` | 200 | answers cite the corpus; **every `Sources` entry has `source_name: null`** (R3-01) |
| step 10: six artifact tables (`entities` 13 rows … `documents` 2), `/artifacts/graph` 13 nodes / 8 edges, row detail | 200 | shipped as documented |
| modify + delete after the index | — | `small.txt` → `modified`, `second.txt` → `removed` (size `null`), preview/tag on the removed row → 404 |
| `docker compose stop postgres; curl /api/ready` | — | `500 Internal Server Error` (R2-22 path — the *only* way the probe fails; R3-04) |
| `sed BOOTSTRAP_ADMIN_PASSWORD; docker compose up -d api` | 0 | log: `Bootstrap admin … skipped: … (BOOTSTRAP_ADMIN_PASSWORD is ignored …)` — README troubleshooting claim holds |
| `python -c socket.connect(('web', 80))` inside the `api` container | — | `ConnectionRefusedError`; `web:8080` connects (R3-02) |
| `uv run python scripts/gen_openapi.py && git diff --stat openapi.json` (Docker recipe) | 0 | no diff — committed contract is fresh |
| `helm lint deploy/helm/graphrag-ui` | 0 | `[INFO] Chart.yaml: icon is recommended` only |
| `helm template` × defaults / ingress / proxy bundled / proxy external / proxy **without** ingress / proxy with empty host | 0 ×6 | Ingress objects 1 / 3 / 2 as the spec says; the last two render **without error** (R3-17) |
| `docker manifest inspect bitnami/postgresql:16.8.0` | 1 | `no such manifest`; `bitnamilegacy/postgresql:16`, `:17.6.0`, `bitnami/postgresql:latest` exist (R3-03) |

## Summary

The product does what the README promises at the API level — every one of the ten quickstart
steps completed from a clean clone, the input freeze and job mutex hold, all four query modes and
the explore surface work on a real index — but the **knowledge-manager layer built on top of it is
broken on the very first index of every project**: R1-67 is not a theoretical race but the normal
path. After a successful `standard` index of two files, both read `skipped`, the overview says
"documents graphrag did not ingest", and every `Sources` citation ships `source_name: null`, so the
citation → document loop that slice 3 exists for never closes (R3-01, P0).

Operations has three P1s an operator hits before any user does: the proxy-auth compose overlay
points oauth2-proxy at `web:80` while nginx moved to 8080 on 2026-09-02 (R3-02); a default `helm
install` pulls a postgres image tag that does not exist (R3-03); and `/api/ready` answers 200 with
`db: "error"`, so the readiness probe only ever fails by accident (R3-04). Below those: the SPA
cannot show *why* a query was refused (rate limit, not indexed) because pre-stream errors never
reach `EventSource` (R3-05); a failed job is reported as the project's "last index" (R3-06); batch
progress is written but never exposed (R3-07); there is no UI to rename a project or change one's
own password (R3-08, R3-09); job history silently stops at 50 rows (R3-10); and the api log is
silent about job lifecycle (R3-11). Deployment hygiene items — no restart policy or limits in
compose, no upgrade/backup runbook, a bootstrap-password placeholder accepted at startup, missing
ingress body-size and NetworkPolicy/ingress interplay — make up the rest of the P2s.

**Counts:** 36 findings — P0 1, P1 3, P2 14, P3 18. Effort: S 25, M 11, L 0.

**Spec-vs-shipped in one line per spec:** core design — shipped with eleven partial/missing
items, mostly operational; hygiene — shipped; i18n — shipped, spec §4.1/§5.1 stale; oauth2-proxy —
backend shipped, **compose overlay broken**, helm guards missing; RBAC — backend shipped, two UI
actions missing; knowledge-manager — slices 1–3 structurally shipped, **state and citation
resolution wrong on first index**, four UI paths missing.

## Spec-vs-shipped matrix

Status vocabulary: **shipped** · **partial** · **missing** · **diverged** (deliberately different,
with the record) · **stale** (spec describes something the code moved past). "Impl" names the file
that implements the section; findings in the last column are this document's unless prefixed R1/R2.

### 2026-08-19 — GraphRAG Web UI design

| § | topic | status | impl | notes / finding |
|---|---|---|---|---|
| 1 | four areas: indexing, settings editor, query, explore | shipped | `api/jobs_routes.py`, `api/settings_routes.py`, `api/query_routes.py`, `api/explore_routes.py` | — |
| 3 | CLI subprocess for index; `graphrag.api` in-process for query; duckdb for artifacts; PG queue with `SKIP LOCKED`; per-project partial unique index; heartbeat liveness; DB-flag cancel; file-tail logs; runner inside the api pod | shipped | `adapters/index_runner.py`, `adapters/graphrag_search.py`, `adapters/artifacts.py`, `adapters/jobs_repo.py:claim_next`, `services/runner_loop.py:28-32` | R2-01/02/06 on the env shielding; R2-27 PID 1 |
| 5 | data model (`users`, `projects`, `project_members`, `jobs`, `settings_versions`, `audit_log`) | shipped (RBAC v2 replaced `users.role`/`project_members.role`) | `adapters/models.py`, migrations | — |
| 5 | job state machine incl. `failed(interrupted)`, `cancelling`, exit 137 → OOM note | shipped | `domain/jobs.py:28-36`, `services/runner_loop.py:46-56` | `display_status` untranslated (R1-19) |
| 5 | cost guardrails: per-user+project rate limit 30/h; launch confirm with last-run runtime/doc count | shipped | `services/rate_limit.py`, `components/JobsPanel.tsx:120-155` | rate-limit refusal invisible in the SPA (R3-05) |
| 6.1 | auth routes, admin user CRUD, project CRUD/members | shipped | `api/auth_routes.py`, `api/users_routes.py`, `api/projects_routes.py` | `PATCH /projects/{id}` has no UI (R3-08); `POST` returns empty `my_permissions` (R3-22) |
| 6.1 | files upload/list/delete, whitelist by `input_file_type`, size + quota | shipped | `api/files_routes.py`, `services/files.py` | effective cap is nginx's 1 MiB (R2-04, README R3-18) |
| 6.1 | settings `GET {content, content_hash}` / `PUT expected_hash` → 409 with current content; `settings_versions` on write | shipped | `api/settings_routes.py:62-102`, `services/settings.py:60-105` | R1-04/05 races |
| 6.1 | `.env` per-key `GET` masked / `PATCH` / `DELETE` | shipped | `api/env_routes.py` | R1-06 |
| 6.1 | jobs `POST`/history, dry-run sync, SSE logs with `Last-Event-ID`, cancel 202 | partial | `api/jobs_routes.py:72-175` | history hard-capped at 50 with no paging (R3-10) |
| 6.1 | query `POST` + `GET …/stream` SSE | shipped | `api/query_routes.py:85-125` | pre-stream errors unreadable by the SPA (R3-05) |
| 6.1 | artifacts: six tables, projection whitelist, paging + filters, row detail, graph by level, `stale`, 409 when no output | shipped | `api/explore_routes.py`, `adapters/artifacts.py` | R1-78 graph memory |
| 6.1 | `/api/health` liveness, `/api/ready` readiness with graphrag version + disk | partial | `api/health_routes.py` | **always 200** (R3-04); R2-22 |
| 6.2 | dual-mode editor (form: LLM/embedding/chunking/storage/vector_store; YAML) with schema validation, dry-run, versions + restore | partial | `components/SettingsPanel.tsx:184-320` | form covers LLM/embedding/chunking + read-only input; no storage/vector_store; validation is YAML syntax + `$` placeholders, no schema (R3-26, R2-03); R1-82 |
| 6.3 | runner: claim under global cap, argv map, stats path by job type, heartbeat 10 s, reconcile 60 s at boot + periodic, cancel SIGTERM → 30 s → SIGKILL, 1 s poll | shipped | `services/runner_loop.py`, `adapters/index_runner.py`, `domain/jobs.py:build_argv` | R2-09/R2-11 cancel edges; no lifecycle logging (R3-11) |
| 6.3 | incremental `stats.json` → real progress (workflows done/total) | missing | — | index jobs expose no progress (R3-36) |
| 6.4 | per-project frame cache with LRU and `QUERY_CACHE_MB`; citations parsed from `[Data: …]`; streaming default; key from workspace `.env` | shipped | `adapters/frame_cache.py`, `domain/citations.py`, `services/query.py` | R1-72 |
| 6.4 | vector-store container-name per-project uniqueness check | missing | — | R3-26 |
| 6.4 | response `{answer, context, citations, timings}`; errors carry a log excerpt | diverged | `api/query_routes.py:31-49` | fixed messages by design (R2-07 explains why); spec stale (R3-31) |
| 6.5 | `input_file_type` locked at creation, synced to `settings.yaml`; whitelist per type; **format change via settings editor with cleanup prompt** | diverged | `adapters/workspace.py`, `services/files.py` | change is not offered anywhere; README states "fixed at creation" — record in spec (R3-26/R3-31) |
| 7 | pages: login, project list, project detail, admin users | shipped (+ AdminRoles, AdminAudit; detail is a routed sidebar per KM §4) | `App.tsx` | — |
| 7 | log viewer: virtual scroll + auto-follow + **pause**; `Last-Event-ID` resume | partial | `components/JobLogViewer.tsx:38-42` | always-follow, no pause, no virtualisation (R3-19; perf R1-85) |
| 7 | query: SSE token stream, expandable citation cards | shipped | `components/tests/AdhocQuery.tsx`, `AnswerView.tsx` | — |
| 7 | settings 409 → **diff** + reload/overwrite | partial | `components/SettingsPanel.tsx:301-330` | two full texts side by side, no diff (R3-26) |
| 7 | explore: Segmented graph/table; sigma + forceatlas2, community colours, level/type/min-degree/search; table with six tables, server paging, detail drawer; stale alert | shipped | `components/ExplorePanel.tsx`, `components/GraphView.tsx` | R1-52, R1-86 |
| 7 | directory layout `features/…` + `shared/` | stale | `components/`, `pages/` | R3-31 |
| 8.1 | compose: postgres, api (runner, workspace volume), web (nginx, `/api` proxy, buffering off) | partial | `docker-compose.yml`, `frontend/nginx.conf` | no restart policy / healthcheck / limits / grace period (R3-12); 1 MiB body cap (R2-04) |
| 8.2 | helm: api `replicas: 1` + `Recreate`, RWO PVC, grace 120 s **+ preStop**, sized limits, OOM 137 special-cased; web deployment; ingress SSE annotations; bundled or external PG; probes | partial | `deploy/helm/graphrag-ui/templates/*` | preStop deferred by comment (`api-deployment.yaml:27`); bundled PG image unpullable (R3-03); no body-size annotation (R3-15); NetworkPolicy blocks ingress → api (R3-16); liveness during migration (R3-29) |
| 8.3 | fixed env-var names shared by both deploy paths; bootstrap admin + forced change | shipped | `config.py`, `docker-compose.yml:16-32`, `api-deployment.yaml:38-88` | placeholder bootstrap password accepted (R3-14); `ACCESS_TOKEN_MINUTES`/`REFRESH_TOKEN_DAYS` undocumented (R1-62) |
| 8.4 | access 15 min, refresh 7 d rotating, hashed in DB, revoke on logout/disable; admin password reset | shipped | `services/auth.py` | R1-68, R2-05, R2-26; no self-service change outside the forced modal (R3-09) |
| 9 | layering; `runner/`, `adapters/graphrag/`, `migrations/` | stale | — | R1-64 |
| 10 | error handling: exit 137 note, reconciler, 409 diff, upload guards, `.env` masking, structured query errors | shipped | as above | — |
| 10 | graphrag CLI missing → readiness reflects **and the SPA pre-checks before launching** | partial | `main.py:_graphrag_version`, `/api/ready` | preflight carries no graphrag status; no UI check (folded into R3-04's recommendation) |
| 10 | retention: logs N days / failed longer; `cache/` per-project cap with manual-cleanup prompt; `update_output/` pruning; **input+output quota pre-check at upload and job start**; disk watermark in readiness + job refusal | partial | `services/retention.py`, `services/jobs.py:35-43,100-115`, `main.py:_retention_loop` | quota checked only at upload; cache warning has no cleanup path (R3-25); readiness never fails on watermark (R3-04) |
| 10 | backup: PG dump + PVC snapshots **documented in Helm values** | missing | — | R3-13 |
| 11 | unit / integration / frontend / smoke tests | shipped | `backend/tests/`, `frontend/src/**/__tests__` | R2-34/35/36 gaps |
| 13 | graphrag pin `==3.1.0`; `nltk_data` baked into the image | stale / shipped | `backend/pyproject.toml` (3.1.2), `backend/Dockerfile:16` | pin row stale (R3-31) |

### 2026-08-23 — Hygiene remediation

| § | topic | status | impl | notes |
|---|---|---|---|---|
| A1 | services own `audit()` + commit; tmp + atomic rename | shipped | `services/files.py`, `services/env_file.py` | R1-05 ordering in `_commit_settings` |
| A2 | `users_routes` stops querying | shipped | `services/users.py` | — |
| A3 | `domain/workspaces.py` + `services.projects.ws_path` | shipped | `domain/workspaces.py` | — |
| A4 | tree walks / `disk_usage` off the loop | shipped | `services/jobs.py:_tree_bytes`, `api/health_routes.py:36` | R1-73, R2-19 leftovers |
| A5.1 | schema-drift test | shipped | `backend/tests/test_schema_drift.py` | — |
| A5.2 | `openapi.json` + `types.generated.ts` committed and diffed; `response_model` ratchet | shipped | `backend/scripts/gen_openapi.py`, `tests/test_openapi_contract.py`, `ci.yml:27-28,49-50` | regen diff clean this session; 8 endpoints still untyped (R1-12) |
| A6 | real-corpus fixtures converge | shipped | `backend/tests/real_corpus_fixtures.py`, `test_real_corpus_guard.py` | — |
| A7 | `ServicePipelineError`; `bodyOf`/`detailOf` | shipped | `services/errors.py`, `api/client.ts:52-100` | — |
| B1–B5 | README + zh-TW mirror, comment sweep, CI comment guard, policy sync | shipped | `README.md`, `docs/zh-TW/README.md` (14/14 headings, same last commit), `tests/test_comment_language.py` | README's ops content is thin (R3-13) |

### 2026-08-24 — i18n

| § | topic | status | impl | notes |
|---|---|---|---|---|
| 4.1 | `ApiError` envelope `{detail, code, params}`; envelope **not** in OpenAPI | shipped | `api/errors.py`; `openapi.json` has no error schema | the brief asks for documented error responses — spec decision to revisit (R3-32) |
| 4.2 | 51-code catalog | stale | 69 codes emitted | R1-63 (undocumented), R1-57 (three uncatalogued) |
| 4.3 | SSE `error` frame with code | shipped | `api/query_routes.py:56` | pre-stream failures are plain HTTP and invisible to `EventSource` (R3-05) |
| 4.4 | non-exception exits carry codes | shipped | `main.py:126`, `api/settings_routes.py:94`, `api/files_routes.py:169` | — |
| 5.1 | TS catalogs, `satisfies` parity, detector `zh*` → zh-TW, verbatim-`detail` invariant | shipped / stale | `i18n/index.ts`, `locales/*.ts` | wire details are English now — invariant no longer holds (R3-31); capitalisation drift (R3-33) |
| 5.2 | antd `ConfigProvider` locale | shipped | `App.tsx:25` | — |
| 5.3 | `Segmented` switcher above logout; `lang`/title sync; dates via `toLocaleString(i18n.language)` | partial | `components/Layout.tsx:67` | R1-65, R1-58, R1-54 |
| 5.4 | `messageOfBody` code → catalog → detail → fallback | shipped | `api/client.ts:66-100` | — |
| 6 | tests | shipped | `i18n.test.ts` etc. | — |

### 2026-08-27 — OAuth2-Proxy authentication

| § | topic | status | impl | notes |
|---|---|---|---|---|
| 4 | `AUTH_MODE`, `PROXY_ADMIN_EMAILS`, `PROXY_AUTH_SECRET` (≥ 32, fail-fast) | shipped | `config.py:39-46` | — |
| 5.1 | resolver: exactly-one secret + email, 401/403 rules, must-change gate skipped | shipped | `api/deps.py` | R2 verified |
| 5.2 | JIT, case-insensitive lookup, `IntegrityError` retry, admin reconciliation every request, bootstrap no-op | shipped | `services/auth.py` | R2-29 cost |
| 5.3 | routes: only `/me` + `/config` in proxy mode; `/config` in `MUST_CHANGE_ALLOWED_PATHS` | shipped | `api/auth_routes.py`, `api/deps.py` | — |
| 5.5 | health endpoints unauthenticated, probes hit the pod directly | shipped | — | — |
| 5.6 | `auth_user_disabled` in both catalogs | shipped | `locales/*.ts` | — |
| 6.1–6.4 | store mode detection, `redirect: "manual"` client, once-per-load redirect, Login/AdminUsers gating, SSE token rule | shipped | `stores/auth.ts`, `api/client.ts`, `pages/Login.tsx:21`, `pages/AdminUsers.tsx:160` | R1-83 (R4 reproduces) |
| 7.1 | compose overlay: pinned image, alpha config, `api_routes`, secrets file, `web` unpublished, **upstream `http://web:80`** | **missing (broken)** | `docker-compose.proxy-auth.yml:57` vs `frontend/nginx.conf:5` | R3-02 |
| 7.2 | helm: hand-rolled oauth2-proxy, split ingresses, `/oauth2` ingress, `injectResponseHeaders`, `emailDomains` render-time guard, three combos lint/template | partial | `templates/oauth2-proxy.yaml`, `templates/ingress.yaml` | no `ingress.enabled`/`host` guard (R3-17); CI renders none of the proxy combos (R3-27) |
| 9 | edge cases documented in README/docs | shipped | `docs/oauth2-proxy.md` | `.internal` wording (R2-38) |
| 10 | test matrix | shipped | `tests/test_proxy_auth.py`, frontend tests | manual smoke runbook exists in `docs/oauth2-proxy.md:113-125` but cannot pass today (R3-02) |
| 11 | AGENTS.md env list, README section, openapi regen | shipped | — | — |

### 2026-08-30 — Composable roles (RBAC v2)

| § | topic | status | impl | notes |
|---|---|---|---|---|
| 4.1–4.2 | atoms, implications, six seeded roles with fixed ids | shipped | `domain/permissions.py`, `domain/role_catalog.py`, migration `654f1c990f8f` | — |
| 4.3 | route → atom table | shipped | every route module (R2 built the full table) | — |
| 5 | schema + migration + lossy downgrade | shipped | migrations `654f1c990f8f`, `5e788ac7d4ad`; `tests/test_rbac_migration.py` | downgrade loss not in any operator doc (R3-13) |
| 6.2 | last-user-manager guard over four mutation classes | shipped | `services/users.py`, `services/roles.py` | R1-75 duplication |
| 6.3 | bootstrap grants, JIT composition, JWT `role` claim dropped | shipped | `services/auth.py` | — |
| 6.4 | audit actions for roles | shipped | `services/roles.py` | — |
| 7 | `UserOut.roles/permissions`, `MemberIn.role_id`, `ProjectOut.my_permissions`, `/api/roles`, `/api/admin/roles`, new codes | shipped | `api/schemas.py`, `api/roles_routes.py` | `POST /projects` returns `my_permissions: []` (R3-22); `user_last_admin_protected` dead in catalogs (R1-57) |
| 8 | AdminUsers multi-select; AdminRoles page with `project:manage` warning and 409 handling; ProjectDetail buttons from atoms; member picker from catalog; nav gating | partial | `pages/AdminUsers.tsx`, `pages/AdminRoles.tsx:115-139`, `pages/ProjectDetail.tsx` | **no project name/description edit** (R3-08); Settings entry hidden below `project:edit_settings` although reads are `project:view` (R3-24); self-service password change absent (R3-09) |
| 9 | tests | shipped | `tests/test_rbac_api.py`, `test_roles_*` | R2-34 gaps |
| 11 | README / zh-TW / oauth2 docs / values / NOTES wording; screenshots | shipped | — | `npm run screenshots` broken (R1-25) |

### 2026-09-06 — Knowledge-manager UX

| § | topic | status | impl | notes |
|---|---|---|---|---|
| 4 | routed second-level sidebar, three groups, badges, `/projects/:id` → overview, atom-gated entries | shipped | `App.tsx:33-43`, `components/project/ProjectSidebar.tsx` | badges never invalidated (R1-18); settings gating (R3-24) |
| 5.1 | `project_files`, `file_tags`, `file_tag_links`; sha256 in the upload loop; discovery with NULL provenance | shipped | `adapters/models.py:178-213`, `services/files.py` | R1-69/70 cost |
| 5.2 | `index_snapshots` start/baseline, `artifact_epoch`, project lock, freeze scoped to index/update, advancement rule | partial | `services/index_snapshots.py`, `services/project_lock.py` | **baseline `attributable_titles` copied from the pre-run row → every first index reads `skipped`** (R3-01 = R1-67 live) |
| 5.3 | question sets, lineages, immutable-once-run, manifest at enqueue, project-shared ratings | shipped (backend) | `services/questions.py`, `services/test_runs.py` | R2-21; **no UI to create/archive a set or archive a question** (R1-02, R3-23) |
| 5.4 | `jobs.params`, `jobs.progress` | partial | `adapters/models.py:174-175`, `services/test_runs.py:278` | `progress` written, **never exposed** (R3-07) |
| 6.1 | rows = `input/` ∪ baseline; nullable `size/modified_at/sha256`; removed rows 404 on file ops | shipped | `services/files.py:list_files` | verified live (removed → 404 on preview/tags) |
| 6.2–6.3 | four states + `skipped` refinement; `ingest_check`; recovery stored at promotion; config freeze incl. `.env` | shipped / wrong | `domain/files.py`, `services/settings.py:120-130`, `services/env_file.py:117` | states `new`/`modified`/`removed` verified live; `skipped` fires for every ingested file (R3-01) |
| 7.1 | files service: lock-around-rename, bulk delete, tags, preview head / `around` | shipped | `services/files.py` | tag routes have no UI (R1-03) |
| 7.2 | `_prepare_query`/`_execute_query`, `runner_loop` dispatch, config loaded once, framed `workspace_config_revision` | shipped | `services/query.py`, `services/runner_loop.py:105-112`, `services/test_runs.py` | R1-08 |
| 7.3 | mutual exclusion incl. test runs; 409 named on both pages | partial | `api/test_runs_routes.py:205-210`, `components/tests/Workbench.tsx:131-137` | jobs page does not name the active job before launch (R3-20) |
| 7.4 | `source_name` with the answer, G0/G1 + epoch guard, batched resolver, locator forms 422, historic bindings 404, passage byte bound | shipped / wrong | `services/citations.py`, `api/files_routes.py:342-377` | resolver runs but the baseline's title set is empty, so `source_name` is always `null` after a first index (R3-01); locator 422 verified live |
| 7.5 | `/health` shape incl. `artifacts_stale`, `regressions`; `/projects/health?ids=` | partial | `services/health.py`, `api/health_project_routes.py` | `last_index` includes failed jobs (R3-06); 200-id cap vs unpaginated list (R3-35) |
| 8 | contract table | partial | `openapi.json` | `PATCH /question-sets/{sid}` absent (R3-23); `GET /jobs?type=` shipped but unused (R1-19) |
| 9.1 | toolbar (search, tags, state filter from `?state=`, quota bar), state column with sentences, banner when `ingest_check` unavailable, frozen affordances, bulk delete confirm, preview drawer, "N not indexed" bar | partial | `components/FilesPanel.tsx`, `components/files/*` | bulk **tag** missing (R1-03); R2-13 |
| 9.2 | ad-hoc mode + batch mode through one `AnswerView`; matrix by lineage, regressions filter, cell drawer, sentence diff, keyboard rating, edit-fork warning, hand-maintained `Citation` | shipped | `components/tests/*` | no run cancel in the workbench (R3-21); no progress (R3-07); R1-84, R1-111 |
| 9.3 | overview action card (8 ordered rules with filtered links), citation click → preview at passage, project-list health column | shipped | `components/project/nextAction.ts`, `pages/ProjectOverview.tsx`, `pages/Projects.tsx:123-141` | card links the active job to the jobs pane, not its log (R1 hand-off; R4 judges) |
| 9.4 | both locales | shipped | `i18n.test.ts` parity | — |
| 10 | test list | shipped | `tests/test_index_snapshots.py`, `test_citation_*`, `test_preview_locator.py`, `test_project_freeze.py` … | none drives `promote` against a real post-run `documents.parquet` (R2-34) — which is why R3-01 escaped |
| 11–12 | release notes (freeze, nullable `FileEntryOut`, baseline needs full `index`), README workflow | shipped | `CHANGELOG.md:9-44`, `README.md:81-129` | — |

## Findings

| id | severity | effort | area | finding | evidence | recommendation |
|---|---|---|---|---|---|---|
| R3-01 | P0 | M | backend/services | **Live reproduction of R1-67 on the happy path, not a race.** A fresh project's first successful `standard` index (two `.txt` files, `stats.num_documents = 2`, `documents.parquet` titles `small.txt`/`second.txt`) promotes a baseline whose `attributable_titles` is `[]` with `title_recovery = "available"`, so `GET /files` reports both files `skipped`, `/health` says `skipped: 2, indexed: 0`, the overview action card tells the user graphrag dropped every document, and every `Sources` citation in all four query modes ships `source_name: null` — the slice-3 citation → document loop never closes. This is what every new project sees on its first index. | quickstart run (Intro table); `psql`: `index_snapshots` rows `start [] / start [] / baseline []`, all `title_recovery = available`; `duckdb`: `documents.title` = `['small.txt','second.txt']`; `services/index_snapshots.py:171` (`attributable_titles=list(start.attributable_titles)`), `:53-79` (`capture_start` reads the *previous* output); R1-67 (`2026-09-19-r1-architecture.md:338`) | Escalate R1-67 to P0 in triage and fix first: `promote()` must re-run title recovery against the post-run `documents.parquet` under the baseline's own `title_recovery`, storing that set (spec §5.2c/§6.3); add the missing slow test that indexes a real corpus and asserts `indexed` + a non-null `source_name`, plus a fast test with a fixture parquet. Until fixed, the README's "Documents" and "loop closed" sections over-promise. |
| R3-02 | P1 | S | deploy | The proxy-auth compose overlay points oauth2-proxy at `http://web:80`, but the web image switched to `nginx-unprivileged` listening on **8080** on 2026-09-02 (commit `27464eb` changed `docker-compose.yml`'s port mapping and left the overlay untouched). Every request through the overlay is a 502; the documented smoke runbook cannot pass. | `docker-compose.proxy-auth.yml:57`; `frontend/nginx.conf:5`, `frontend/Dockerfile:14-16`; `git log -S'nginx-unprivileged' -- frontend/Dockerfile` → `27464eb`; live: `socket.connect(('web',80))` → `ConnectionRefusedError`, `('web',8080)` connects; spec §7.1 (`2026-08-27:394`) also says `web:80` | Change the upstream to `http://web:8080` (and the spec line); add the overlay to the compose smoke job R2-15 proposes so a port change cannot silently orphan it again. |
| R3-03 | P1 | S | deploy | A default `helm install` cannot start: the bundled postgresql dependency renders `image: registry-1.docker.io/bitnami/postgresql:16.8.0`, which does not exist on Docker Hub (Bitnami's 2025 reorg) — the chart's own `values.yaml` comment says so and ships the broken default anyway. Operators who follow README → Helm get `ImagePullBackOff` on the DB and an api pod that never becomes ready. | `deploy/helm/graphrag-ui/values.yaml:72-76`; `helm template` default output line 555; `docker manifest inspect bitnami/postgresql:16.8.0` → `no such manifest`; `bitnamilegacy/postgresql:16` and `:17.6.0` exist | Default `postgresql.image.registry/repository` to a pullable source (`bitnamilegacy/postgresql` with a tag verified by `docker manifest inspect`, or the `bitnami/postgresql:latest` line the reorg left), or `required`-guard the image so the render fails with the reason; state it in `NOTES.txt` and the README Deployment section; add a CI step that `docker manifest inspect`s every image the default render references. |
| R3-04 | P1 | S | backend/api | `/api/ready` returns HTTP 200 whatever it finds: `{"db": "error", …}` and `"disk_ok": false` are 200 bodies, so the Helm readiness probe (`httpGet`, status-code based) never fails for the conditions the endpoint exists to report; the only way it fails today is the unhandled `OSError` 500 of R2-22 (reproduced: postgres stopped → 500 with a traceback). A pod whose DB credentials broke or whose volume crossed the watermark keeps receiving traffic, and spec §10's "refuse new jobs and alert" has no alerting half. | `api/health_routes.py:22-41` (`db = "error"` then `return {…}` with no status); `deploy/helm/graphrag-ui/templates/api-deployment.yaml:100-104`; live `docker compose stop postgres; curl /api/ready` → `500 Internal Server Error` | Return 503 when `db != "ok"` or `not disk_ok` or graphrag is `not-installed` (keep the JSON body), catch `OSError` alongside `SQLAlchemyError` (R2-22), declare a `ReadyOut` response model (R1-12), and make the preflight/launch UI read the same `graphrag` field (spec §10 pre-check). Test: patched session factory raising → 503. |
| R3-05 | P2 | M | frontend/components | When the query stream is refused before the first frame — 429 `query_rate_limited`, 409 `not_indexed`, 500 `query_config_failed`, 403 — the backend answers with a plain JSON error (i18n spec §4.3 "pre-stream failures keep plain bodies"), `EventSource` fires `error` with no body, and the SPA can only say "Query failed — try again later". A user who hit the 30/hour limit, or whose project has no index, is told to retry; the codes and both locale strings exist but never render. | `api/query_routes.py:105-111` (`raise _query_error_http(exc)` before `StreamingResponse`); `components/tests/AdhocQuery.tsx:66-84` (comment acknowledges it; falls back to `query.failedRetry`); catalog has `query_rate_limited`/`not_indexed` in both locales | Either (a) emit pre-stream failures as a 200 `text/event-stream` whose first frame is `event: error` with `{detail, code}` (one code path, SSE spec already allows it), or (b) on `EventSource` error before any frame, have the SPA issue `POST /query`-shaped probe (`HEAD`/`OPTIONS` is not enough — it needs the body) or a cheap `GET /health` + `/jobs/preflight` read to explain the likely cause. (a) is smaller and testable in `test_query_stream_sse.py`; document it in i18n spec §4.3. |
| R3-06 | P2 | S | backend/services | `/health.last_index` (and the batch `last_index.finished_at` on the project list) is the most recently **finished** `index`/`update` job regardless of status, so a failed or cancelled job renders on the overview as "Last index: index, finished 08:39" and on the list as a fresh index date. Reproduced: after the fake-key failure the health JSON reported that job as `last_index`. | `services/health.py:50-60` (`Job.finished_at.is_not(None)`, no status filter); `pages/ProjectOverview.tsx:76-84` (`overview.lastIndexLine` shows type + time only); live health JSON in the Intro table | Filter to `status == "succeeded"` for `last_index` and add a separate `last_attempt: {status, finished_at}` (or include `status` and render "last attempt failed" in red); regen `openapi.json`/types; test with a failed job newer than a succeeded one. |
| R3-07 | P2 | S | backend/api | Batch progress is written but never shown: the test-run worker updates `jobs.progress = {done, total}` between questions (spec §5.4 "so the UI can show batch progress"), but `JobOut` has no `progress` field, `/test-runs` does not carry it, and the workbench polls the matrix every 2 s with no "3 / 20" anywhere. A 200-question run is a spinner with no ETA. | `services/test_runs.py:278` (`set_progress`); `api/schemas.py:138-157` (`JobOut` fields end at `argv`); `components/tests/Workbench.tsx:113-125`; `grep -rn progress frontend/src` → 0 consumers | Add `progress: {done, total} \| null` to `JobOut` and to the matrix's running-run column header/banner; regen contract; test the field round-trip. |
| R3-08 | P2 | M | frontend/pages | No UI edits a project's name or description: `PATCH /api/projects/{id}` (RBAC §8 "project name/description edit → `project:manage`") has no caller; the Members pane shows a read-only `Descriptions`. A typo in the name at creation is permanent from the SPA. | `grep -rn 'method: "PATCH"' frontend/src` → only env, questions, users, roles; `pages/ProjectDetail.tsx:330-350` (`ProjectInfoDescriptions`); `api/projects_routes.py:149` | Add an "Edit" action on the members pane for `canManage` (modal with name + description, `PATCH`, invalidate `["projects", id]` and the list); test in `ProjectDetail` suite. |
| R3-09 | P2 | S | frontend | A logged-in user cannot change their own password: `/api/auth/change-password` is called only from the forced modal on `Login.tsx` when `must_change_password` is set; `Layout`'s user menu has just "logout". RBAC §4.1 lists "own profile and password management" as the baseline for every active user, and spec §8.4's "admin resets" is the recovery path, not the routine one. | `grep -rn change-password frontend/src` → `pages/Login.tsx:38` only; `components/Layout.tsx:57` (menu items = `logout`); `api/auth_routes.py:133` | Add "Change password" to the Layout dropdown in local mode (reuse the Login modal's form + `api()` instead of raw `fetch`, R1-55); hide in proxy mode. |
| R3-10 | P2 | M | backend/api | Job history is silently truncated: `GET /projects/{pid}/jobs` returns the newest 50 rows with no `limit`/`offset`/`total`, the SPA renders them with `pagination={false}`, and `test_run` rows count against the 50 (R1-19 shows them in the same table). A team that runs the question set daily loses every index job from the page within two months, and the API offers no way to page. `settings/versions` has the same silent cap (`VERSIONS_PAGE_CAP = 50`) while `audit` and `artifacts` paginate properly — limits are inconsistent across list endpoints. | `adapters/jobs_repo.py:135-142`; `api/jobs_routes.py:96-116` (only `type` filter); `components/JobsPanel.tsx:67-80,231-236`; `services/settings.py:23,153-158`; contrast `api/audit_routes.py:36-37`, `api/explore_routes.py:89-90` | Add `limit` (≤ 200) + `offset` + `total` envelope to jobs and settings-versions (coordinate with R1-45's envelope decision), default the jobs page to `type=index&type=update` (or exclude `test_run`) and paginate the antd table; document the caps in the route docstrings so they reach `openapi.json`. |
| R3-11 | P2 | M | backend/services | The api process logs nothing an operator can act on for the job lifecycle: no line when a job is claimed, spawned, finishes (status/exit code/duration), is cancelled, reconciled as `failed(interrupted)`, or when the retention sweep runs and what it reclaimed. During the quickstart's failed index the container log contained only uvicorn access lines; the failure exists solely in `jobs.error` and the per-job log file. There is also no logging configuration — the bootstrap message is a bare `logger.warning` without level or timestamp, and app-level `INFO` would be dropped by Python's default root level even if added. | live `docker compose logs api` (Intro table); `grep -n 'logger\.' services/runner_loop.py adapters/index_runner.py services/retention.py` → only `warning`/`exception` on internal errors; `main.py` has no `logging.basicConfig`/`dictConfig`; `services/auth.py:243` | Configure logging once in `main.py` (uvicorn-compatible format with level + timestamp, root at INFO) and emit INFO events: `job claimed`, `job spawned pid=…`, `job finished status=… exit=… duration=…`, `job cancel requested`, `reconciled N stale jobs`, `retention swept N logs / M snapshots`, `bootstrap admin created`. A `LOG_LEVEL` knob would need the AGENTS.md env list amended — decide in the spec. |
| R3-12 | P2 | S | deploy | The compose `api` service has no `restart` policy (a host reboot or a crashed migration leaves the stack down until someone runs `up`), no `healthcheck` (so `web`'s `depends_on` cannot wait for readiness and `docker compose ps` never shows unhealthy), no memory limit (spec §8.2's OOM sizing exists only in Helm; on compose an indexing spike swaps the host instead of killing the job), and no `stop_grace_period` (the default 10 s SIGKILLs an in-flight index — moot until R2-27's PID-1 fix lets SIGTERM reach uvicorn at all). | `docker-compose.yml:14-36` (api block), `:37-40` (web); Helm contrast `values.yaml:16-18`; R2-27 | Add `restart: unless-stopped` to all three services, `healthcheck` on `/api/health` for `api` and `condition: service_healthy` on `web`, `deploy.resources.limits.memory: 2g` (or `mem_limit`) mirroring the chart, and `stop_grace_period: 120s` once PID 1 is uvicorn. |
| R3-13 | P2 | M | docs | There is no operations runbook. Nothing in `README.md`, `values.yaml`, `NOTES.txt` or `docs/` covers: how an upgrade behaves (migrations run at container start; a failing migration exits the api container / CrashLoopBackOffs the pod, and with `strategy: Recreate` the old pod is already gone — the app is down with no rollback, and the RBAC downgrade is documented as lossy only in a migration docstring); how to back up and restore (Postgres dump + the workspaces volume, which spec §10 says must be "documented in Helm values"); what to watch in logs (nothing, R3-11); how to size resources for compose. The README "Deployment" section is two bullets. | `README.md:264-272`; `deploy/helm/graphrag-ui/values.yaml` (no backup/upgrade text); spec `2026-08-19:303`; migration `5e788ac7d4ad` docstring; `deploy/helm/graphrag-ui/templates/api-deployment.yaml:10-11` | Add a README "Operations" section (mirrored in zh-TW): upgrade procedure (back up first, `pull`/`up -d`, watch `alembic` lines, rollback = restore DB + previous image, note lossy revisions), backup/restore commands for both deploy paths, log expectations, resource sizing; point `values.yaml` and `NOTES.txt` at it. |
| R3-14 | P2 | S | backend/config | `.env.example` ships `BOOTSTRAP_ADMIN_PASSWORD=bootstrap-admin-change-me` and the app accepts it: `Settings` rejects a placeholder `JWT_SECRET` but not the placeholder bootstrap password, so a deployment that only replaced `JWT_SECRET` (the README lists all three, but the compose `:?` guard only enforces presence) runs with a publicly known admin credential until someone logs in and is forced to change it — and whoever logs in first owns the deployment. | `.env.example:18-21`; `config.py:10,60-72` (only `jwt_secret` is checked); `docker-compose.yml:19-20` (`:?` presence only); `services/auth.py:218-273` | Reject the shipped placeholder (and passwords < 12 chars) in the same `model_validator`, log which variable failed; keep `must_change_password` as the second line. |
| R3-15 | P2 | S | deploy | The Helm ingress carries no `nginx.ingress.kubernetes.io/proxy-body-size` annotation, so nginx-ingress's default `1m` rejects uploads over 1 MiB with an HTML 413 before the request reaches the web nginx — the Kubernetes twin of R2-04, and it survives R2-04's fix. `uploadMaxFileMb` (default 50) is therefore false advertising on Helm. | `deploy/helm/graphrag-ui/templates/ingress.yaml:12` (`$sse` dict has only buffering/read-timeout); `values.yaml:100` | Render `proxy-body-size: "{{ .Values.uploadMaxFileMb }}m"` into `$sse` (both ingress objects), mention it in `NOTES.txt`, and assert it in the helm CI step. |
| R3-16 | P2 | S | deploy | With `ingress.enabled=true` and the default `networkPolicy.enabled=true`, the ingress controller cannot reach the api pod: the ingress routes `/api` **directly** to the api Service, but the NetworkPolicy admits only the web and oauth2-proxy pods unless `networkPolicy.extraIngressFrom` names the controller's namespace — which nothing enforces or prints. On a policy-enforcing CNI every `/api` call through the ingress times out while the SPA shell loads fine, which reads as a backend outage. | `deploy/helm/graphrag-ui/templates/ingress.yaml:41-46`; `templates/networkpolicy.yaml:27-41`; `values.yaml:55-59` (comment-only hint); `NOTES.txt` silent | Either route `/api` through the web Service (nginx already proxies it, which also drops the `api` ExternalName alias) or `fail`/warn in `NOTES.txt` when `ingress.enabled` and `extraIngressFrom` is empty; document the required selector for ingress-nginx. |
| R3-17 | P2 | S | deploy | Two proxy-auth misconfigurations render silently: `proxyAuth.enabled=true` without `ingress.enabled` renders a full release (api in `AUTH_MODE=proxy`, oauth2-proxy with a static upstream) in which no path carries the identity headers, so every request is 401 with nothing to fix in the app; and bundled mode with an empty `ingress.host` renders `auth-signin: https:///oauth2/start?rd=…`. `values.yaml` says both are required; nothing checks. Separately, `NOTES.txt` step 2 tells every operator to log in with the bootstrap admin, which is a no-op in proxy mode. | `helm template … --set proxyAuth.enabled=true` (no ingress) → exit 0, `AUTH_MODE` present; `--set ingress.enabled=true --set proxyAuth.enabled=true` (no host) → `nginx.ingress.kubernetes.io/auth-signin: https:///oauth2/start?rd=$escaped_request_uri`; `templates/ingress.yaml:9`; `values.yaml:116-117`; `templates/NOTES.txt:14-17`; spec §5.2 "bootstrap_admin is a no-op in proxy mode" | `fail` in `oauth2-proxy.yaml`/`ingress.yaml` when `proxyAuth.enabled` and (`not ingress.enabled` or (bundled and `not ingress.host`)); branch NOTES step 2 on `proxyAuth.enabled` ("sign in through the IdP; `proxyAuth.adminEmails` holds the admins"). |
| R3-18 | P2 | S | docs | README quickstart step 6 says the per-file cap is `UPLOAD_MAX_FILE_MB` (default 50) and exceeding it yields 413; the shipped stack's effective cap is nginx's 1 MiB with an HTML 413 (R2-04), reproduced in this run with a 2.2 MB text file. Anyone following the README with a real document hits it at step 6. `.env.example:23-24` and `values.yaml:100` make the same claim. | `README.md:160-161`; `docs/zh-TW/README.md` mirror; Intro table row 4; R2-04 | Fix R2-04 (and R3-15) and keep the README; until then add the caveat next to step 6 in both languages. |
| R3-19 | P3 | S | frontend/components | The log viewer has no pause/follow control: every incoming chunk sets `scrollTop = scrollHeight`, so while a job runs the user cannot scroll up to read an earlier error — the view snaps back on the next line. Spec §7 lists "auto-follow + pause" and virtual scrolling; neither exists (perf side is R1-85). | `components/JobLogViewer.tsx:38-42`; spec `2026-08-19:220` | Track a `follow` flag that turns off when the user scrolls away from the bottom and back on via a "Follow" button/when they return to the bottom; virtualise with the existing antd `List`/a windowing lib once R1-85 is scheduled. |
| R3-20 | P3 | S | frontend/components | The jobs page does not tell the user a job is already running before they click Start: `preflight.active_job` is fetched and ignored by `confirmLaunch`, so launching during a test run (or an index) ends in a 409 `job_conflict` toast after the confirm modal. Spec §7.3 says the conflict is named "on the jobs page" too, as the workbench does. | `components/JobsPanel.tsx:120-155` (reads `last_run`, `cache_*`, `disk_*`, never `active_job`); `components/tests/Workbench.tsx:131-137` (the workbench version); spec `2026-09-06:867` | Render the workbench's "a {type} job is running" notice above the launch controls and disable Start while `active_job` is set; share `jobTypeLabel` (R1-49). |
| R3-21 | P3 | S | frontend/components | A running test run cannot be cancelled from where it is watched: the workbench shows the running column filling in but no Cancel; the only cancel is the jobs pane, where the row is labelled with the raw `test_run` (R1-19). Spec §7.3 promises the batch is "cancellable". | `grep -n cancel components/tests/Workbench.tsx` → only modal `cancelText`; `components/JobsPanel.tsx:196-199` | Add Cancel to the running-run banner/column header calling `POST /jobs/{id}/cancel` (the run's `job_id` is in `TestRun`); reuse the jobs pane confirm copy. |
| R3-22 | P3 | S | backend/api | `POST /api/projects` returns `my_permissions: []` for the creator while `GET /api/projects/{id}` and the list return the owner's five atoms — the contract lies for one round trip. The SPA survives because it invalidates and refetches, but any client (or a future "navigate to the new project" flow) that trusts the create response renders a project with no actions. | live: create → `"my_permissions":[]`, GET → five atoms; `api/projects_routes.py:138` vs `:143-147` | Compute `my_permissions` in `post_project` as `get_one` does (owner ⇒ all five); assert in `test_projects.py`. |
| R3-23 | P3 | M | frontend/components | Question-set management is one-way beyond R1-02: sets cannot be renamed (spec §8 lists `PATCH /question-sets/{sid}`; the route does not exist), archived (`DELETE /question-sets/{sid}` exists, no caller), and questions cannot be archived from the SPA (`DELETE …/questions/{qid}` exists, no caller). A mistyped question or an obsolete set stays in the picker forever. | `openapi.json` (no `PATCH /question-sets/{sid}`); `grep -rn 'method: "DELETE"' frontend/src` → files, env, members, roles, projects only; `api/questions_routes.py` | Schedule with R1-02 as one "question-set lifecycle" wave: create/rename/archive set, archive question (with the lineage warning), and the missing `PATCH` route + contract regen. |
| R3-24 | P3 | S | frontend/components | The Settings sidebar entry requires `project:edit_settings`, but every read it hosts — `GET /settings`, `/settings/versions`, `/env` key names — is gated on `project:view` (RBAC §4.1), and `SettingsPanel` already handles `canEdit=false`. A `maintainer` or `viewer` cannot see which model or chunk size the project uses (the URL still works, so the hiding is inconsistent rather than protective). | `components/project/ProjectSidebar.tsx:18`; `api/settings_routes.py:68,111,131`, `api/env_routes.py:76`; `pages/ProjectDetail.tsx:288` (`canEdit={canEditSettings}`) | Gate the entry on `project:view`; keep the write buttons on `project:edit_settings`. |
| R3-25 | P3 | M | backend/services | Spec §10's resource governance is half-wired: `enqueue` checks only the disk watermark, never the project's `input/ + output/` quota it promises to pre-check "before starting a job" (an over-quota project can still start an index that grows `output/`); the `cache/` cap is a launch-time warning with no way to act — no "clear cache" action in the UI or API and no documented procedure — so the warning repeats on every launch until an operator `rm -rf`s inside the volume. | `services/jobs.py:28-52` (watermark only), `:100-115` (preflight reports `cache_bytes` only); `components/JobsPanel.tsx:123-140`; spec `2026-08-19:296-301` | Add `usage_bytes`/`quota_bytes` to preflight and refuse enqueue over quota (`quota_exceeded`), and either a `POST /projects/{pid}/cache:clear` (`project:run_jobs`, refused while a job is active) with a button on the warning, or a README procedure. |
| R3-26 | P3 | M | spec | Settings-editor deviations to record or close: form mode lacks the storage and vector_store blocks §6.2 lists; no per-project vector-store container-name uniqueness check (§6.4 — harmless with the default LanceDB path, wrong for Azure AI Search/Cosmos users); the 409 modal shows two full texts rather than a diff (§7); and changing `input_file_type` via the editor (§6.5) is not offered — README says "fixed at creation", so this one is a deliberate divergence the spec never recorded. | `components/SettingsPanel.tsx:184-330`; `services/settings.py:60-105`; spec `2026-08-19:205,213-217,222`; `README.md:157-158` | Amend the spec (§6.5 → locked at creation; §6.2 → the three form blocks shipped, storage/vector_store deferred), and either implement a line diff in the conflict modal (a 30-line pure function) or drop the word "diff" from §7. |
| R3-27 | P3 | S | ci | CI renders the chart only with defaults and the external-DB combo; the proxy-auth spec's three value sets (bundled, external, disabled) with the Ingress-count assertions are not gated, and neither `docker compose config` of the overlay nor anything else would have caught R3-02, R3-03 or R3-17. | `.github/workflows/ci.yml:115-121,94-105`; spec `2026-08-27:631-638` | Add the two proxy renders with `grep -c 'kind: Ingress'` assertions, a render with `--set ingress.enabled=true` asserting the body-size annotation (R3-15), a `docker manifest inspect` of the default images (R3-03), and — when R2-15 lands — boot the overlay and curl `/api/auth/me` for the 401 the runbook expects. |
| R3-28 | P3 | S | deploy | The web image builds on `node:26-alpine` while its own first comment, CI (`node-version: 24`) and AGENTS.md pin Node 24 — the drift the comment warns about, already present. | `frontend/Dockerfile:1-3`; `.github/workflows/ci.yml:38,70`; `AGENTS.md` Commands | Use `node:24-alpine` (or move CI and AGENTS.md to 26 together). |
| R3-29 | P3 | S | deploy | The api pod has no `startupProbe`; liveness starts at 30 s with `failureThreshold: 5 × 10 s`, so a startup whose `alembic upgrade head` (run in the container `CMD`) takes longer than ~80 s — a large migration, a slow managed Postgres — is killed mid-migration and restarted in a loop, each attempt re-running the same migration. | `deploy/helm/graphrag-ui/templates/api-deployment.yaml:95-104`; `backend/Dockerfile:44` | Add a `startupProbe` on `/api/health` with `failureThreshold: 60, periodSeconds: 5` and drop `initialDelaySeconds`; long-term, run migrations in a Helm hook Job (also fixes the Recreate downtime ordering, R3-13). |
| R3-30 | P3 | S | deploy | Chart ergonomics gaps an operator meets on day one: the PVC has no `storageClassName`/`existingClaim` knob (default class or nothing); the `api` ExternalName alias means one release per namespace and is documented only in a template comment; `api.image`/`web.image` default to `graphrag-ui-api:0.1.0`/`graphrag-ui-web:0.1.0`, which no registry publishes and the README never explains how to build and push. | `templates/pvc.yaml`; `templates/api-service-alias.yaml:1-8`; `values.yaml:10,40`; `README.md:264-268` | Add `persistence.storageClassName`/`existingClaim`; print the one-release-per-namespace constraint in `NOTES.txt` (or fix R3-16's routing and delete the alias); add a "build and push images" paragraph to the Deployment section. |
| R3-31 | P3 | S | spec | Spec statements the code moved past, beyond R1-64: main spec §7 directory layout (`features/`, `shared/`) vs `components/`+`pages/`; §13 row 3 "pinned `==3.1.0`" vs `3.1.2`; §6.4 "errors carry a log excerpt" vs fixed messages (deliberate, R2-07); §6.5 format change via the editor vs locked at creation; i18n §5.1's verbatim-`detail` invariant (wire details are English now, `api/client.ts:53-56` describes the current design); i18n §4.1's "envelope not declared" vs the contract goal (R3-32); KM §8's `PATCH /question-sets/{sid}` (R3-23); proxy §7.1's `web:80` (R3-02); proxy §9's `.internal` (R2-38). | `2026-08-19:224,328,205-207,213-217`; `2026-08-24:87-96,250-258`; `2026-09-06:1198`; `2026-08-27:394,577-581` | Add a dated "Errata / as-built" section to each spec rather than editing history (hygiene spec B2 policy); one PR. |
| R3-32 | P3 | M | backend/api | `openapi.json` documents no 4xx/5xx for any route and no error envelope: the only non-2xx schema is FastAPI's `HTTPValidationError`; `ApiErrorBody` is hand-written in `types.ts`. The i18n spec chose this (§4.1) to keep the generated artifact unchanged, but the result is that the contract the brief calls authoritative says nothing about 401/403/404/409/413/429, and every problem code lives outside it. | `python` scan of `openapi.json`: 1 documented non-2xx (the 202 on cancel), `components.schemas` error keys = `['HTTPValidationError','ValidationError']`; `frontend/src/api/types.ts:97-101`; spec `2026-08-24:87-96` | Declare an `ApiErrorBody` pydantic model and a shared `responses=` helper per status (or a global `openapi()` post-processor that attaches it to every route); regen contract + types and delete the hand-written interface. Coordinate with R1-12/R1-44/R1-45 as one contract wave. |
| R3-33 | P3 | S | frontend/i18n | en-US error copy mixes sentence case with lowercase fragments — `email already registered`, `role is still granted…`, `built-in roles are immutable`, `a role with that name already exists`, `role not found`, `invalid permission set`, `role scope mismatch`, `cannot remove the last active user manager`, `cannot change your own role…`, `key not found`, `key and value are required`, `value must be a single line`, `value too large` — beside `Project not found`, `Invalid body`; `project_indexing` carries `params.job_type` that neither locale interpolates. zh-TW mixes 「key」/「token」 English nouns with full sentences (R1-66 territory). | catalog dump (this session) of `locales/en-US.ts:40-110` and `zh-TW.ts`; `services/errors.py:22` (`params = {"job_type": …}`) | Normalise to sentence case, use the `job_type` param ("An index job is running…"), and add a catalog lint (regex on the first character) to `i18n.test.ts`. |
| R3-34 | P3 | S | backend/services | Audit inconsistency beyond R2-23, observed live: the quickstart's trail holds `user.created`, `project.created`, `file.uploaded/deleted`, `env.key_set` — but nothing for the forced password change, nothing for the two index jobs (enqueue, failure, success), while `test_run.enqueued` *is* audited. An operator asking "who started the job that spent last night's budget" or "when was the admin password changed" gets no answer from the audit page. | live `GET /api/admin/audit` (Intro); `services/jobs.py:enqueue/cancel` (no `audit()`); `api/auth_routes.py:133-160` (no audit); `services/test_runs.py:161` (audited) | Decide the audit scope in the spec (R2-23) and, if "state-changing → audited" is the rule, add `job.enqueued`/`job.cancelled`/`password.changed`/`password.reset` rows. |
| R3-35 | P3 | S | frontend/pages | The projects list is unpaginated and asks `/api/projects/health?ids=` for every visible id, while that endpoint 422s above 200 ids (`health_too_many_ids`, uncatalogued — R1-57) and the SPA swallows the error (`r.ok ? … : null`), so past 200 projects the health column silently blanks. Unlikely at 10–50 users; cheap to bound. | `pages/Projects.tsx:43-49,199`; `api/health_project_routes.py:31,130-135` | Chunk the ids by 200 in the query (or paginate the list); surface the failure as a muted "health unavailable" cell. |
| R3-36 | P3 | M | backend/domain | Index and update jobs expose no progress although graphrag writes `stats.json` incrementally per workflow (spec §6.3 records this precisely so the UI could show "workflow 5/12"); the jobs table shows a `running` tag for jobs that take 30 minutes to hours, and the sidebar badge is a `1`. | spec `2026-08-19:172-174,353`; `adapters/index_runner.py` (stats read only at exit); `jobs.progress` column exists (`adapters/models.py:178`) | Have the watch loop parse `output/stats.json` (`update_output/<ts>/delta/stats.json` for updates) every heartbeat into `jobs.progress = {done: len(workflows), total: …}` and render it with R3-07's field. |

## Quickstart run — step by step

Clean clone of `main@3fa8d3a`, README followed verbatim; deviations from the README's text are
called out.

1. **Prerequisites** — Docker 29.4 / Compose v5.1.2. Node and uv were not needed (as stated).
2. **Configure** — `cp .env.example .env`; set `JWT_SECRET` (`openssl`-equivalent 64 hex),
   `BOOTSTRAP_ADMIN_EMAIL=admin@r3review.example.com`, `BOOTSTRAP_ADMIN_PASSWORD`.
   `docker compose config --quiet` → OK. Note the shipped placeholder password would also have
   been accepted (R3-14).
3. **Start** — `docker compose up --build -d`: 1 m 54 s cold. Postgres healthy first; api ran ten
   migrations (`77c4809ab999 … e46955e86d60`) and served `/api/health` 200, `/api/ready`
   `{"db":"ok","graphrag":"3.1.2",…}`. SPA served at `:8080` with the CSP header; deep links
   (`/projects/x/files`) return `index.html`. `index.html` still carries `lang="zh-Hant"` (R1-58).
4. **First login** — `POST /auth/login` 200 with `must_change_password: true`; `/api/projects`
   → 403 `auth_must_change_password`; `POST /auth/change-password` 204; then 200. Works as
   documented. (No audit row for the change — R3-34.)
5. **Create project** — `text` project 201 in 0.9 s (`graphrag init` inside). The response carried
   `my_permissions: []` (R3-22).
6. **Upload** — 78 B `.txt` → 201; `.csv` → 400 `file_ext_not_allowed` with params; **2.2 MB
   `.txt` → nginx HTML 413** (R2-04 / R3-18). The README's "exceeding either → 413" is true only
   above 1 MiB, for the wrong reason.
7. **Set the LLM key** — `GET /env` showed graphrag init's placeholder `<A****`; `PATCH` 204;
   read-back `sk****`. Dry-run 200 in 6.5 s (it passes with an invalid key — it never calls the
   LLM, which the README does not say).
8. **Index** — with a fake key: job `failed`, exit 1, `jobs.error` = litellm's
   `AuthenticationError` tail, SSE log delivered the same line and a `done` frame; the api log
   showed nothing (R3-11). Mutex (409 `job_conflict`) and input freeze (409 `project_indexing`
   with `params.job_type`) both held. With the real key, `standard`: `succeeded` in 50 s
   (`total_runtime` 27.8 s, `num_documents` 2). **Both files then read `skipped`** and health
   `skipped: 2` (R3-01). The README caveat about `fast` on tiny corpora was not exercised.
9. **Query** — `local` 6.5 s, `global` 3.1 s, `drift` 66.5 s, `basic` 2.6 s, all 200 with
   answers grounded in the corpus and `[Data: …]` citations parsed into `Sources`/`Entities`/
   `Reports`/`Relationships`; the SSE stream delivered token frames. **`source_name` was `null`
   on every `Sources` entry** (R3-01). Six scripted queries stayed under the 30/hour limit; the
   limit's refusal would have been invisible in the SPA (R3-05).
10. **Explore** — six tables with the documented projections (`documents` without `text`), row
    detail with full columns, graph 13 nodes / 8 edges at level 0, `stale: false`. As documented.

After the ten steps: re-uploading a changed `small.txt` → `modified`; deleting `second.txt` →
`removed` with `size: null`, still counted in `total`, 404 on preview/tag — the §6.1 contract
holds. `/health.last_index` still named the earlier *failed* job until the real one finished
(R3-06).

**Troubleshooting section** — the "invalid email or password after re-running `up`" entry is
accurate: with `BOOTSTRAP_ADMIN_PASSWORD` changed, the api logged `Bootstrap admin … skipped: …
already holds users:manage (BOOTSTRAP_ADMIN_PASSWORD is ignored …)`. The `npm ci`/`.npmrc` entry
matches `frontend/Dockerfile:5-6`. The LiteLLM warning entry could not be checked (no such line
appeared in this run's logs, consistent with `LITELLM_LOG=ERROR`).

**Not exercised:** `update` jobs (a second paid run; R4/V), the workbench batch run (R4), the
proxy-auth overlay end-to-end (no IdP; and R3-02 makes it impossible today).

## Error codes vs. what the SPA shows

Method: every `ApiError(...)`, `"code": ...`, `self.code = ...` and coded-exception literal in
`backend/src` (69 codes) cross-checked against the `errors` section of both catalogs (66 keys)
and against the four specs.

- **Catalog gaps** — `citation_not_found`, `health_invalid_ids`, `health_too_many_ids` render the
  English `detail` in the zh-TW UI; `user_last_admin_protected` is a dead entry. Already R1-57;
  nothing new.
- **Spec gaps** — nine codes in no spec (R1-63); confirmed unchanged.
- **Codes that exist but never reach the user** — `query_rate_limited`, `not_indexed`,
  `query_config_failed` and `forbidden` on the *stream* route (R3-05); `file_too_large`/`quota_exceeded`
  are pre-empted by nginx's HTML 413 for anything over 1 MiB (R2-04); FastAPI 422 bodies are
  rendered raw and echo input (R1-80).
- **Copy quality** — both locales carry real sentences for the KM vocabulary (five index states,
  three `ingest_check` reasons, two 409s — verified in `components/files/indexState.tsx` and the
  catalogs); en-US capitalisation is inconsistent and `project_indexing`'s param is unused
  (R3-33); `query_failed`/`dry_run_failed`/`init_failed` are fixed strings with the diagnostic kept
  server-side by design (R2-07) — the operator-side half of that design (R3-11) is missing.
- **`settings_conflict`** is handled by the modal, not a toast (correct); `job_conflict` on the
  jobs page arrives after the confirm modal (R3-20).

## Verified OK

- **Input freeze and job mutex** hold end-to-end through nginx: second enqueue → 409
  `job_conflict`; upload during a queued job → 409 `project_indexing` with `params.job_type`;
  `.env` and settings writes share the lock (`services/settings.py:120-130`,
  `services/env_file.py`). `test_run` jobs do not freeze input (`services/project_lock.py:21`).
- **Removed-row contract** (KM §6.1): after a delete the name stays listed as `removed` with
  `size`/`modified_at`/`sha256` null, counted in `total`; preview and tag routes 404 for it.
- **Preview locator forms** (KM §7.4): `GET` → head window with `match: false`; `POST {passage}`
  → `match: true`; half a historic pair → 422 (pydantic, `extra="forbid"`).
- **Explore surface** matches spec §6.1/§7 column projections, paging and `stale`; graph
  `levels`/`truncated`/`node_limit` envelope present.
- **Query modes**: all four answer on a real index; streaming yields per-token `chunk` frames,
  `citations` and `done` with timings; the non-streaming body is `{answer, context, citations,
  timings}`.
- **Readiness content**: `graphrag` version is detected once at startup (`main.py:34-46`) and
  reported as `3.1.2`; `disk_free_mb`/`disk_ok` reflect the watermark. (Status code is R3-04.)
- **Bootstrap idempotence** and the README troubleshooting text (see Quickstart).
- **Compose default config** is byte-stable (`docker compose config --quiet` clean) and the
  overlay config renders (its runtime failure is R3-02, not a config error).
- **Helm**: lint clean; Ingress counts 1/3/2 for disabled/bundled/external match proxy spec §7.2;
  `emailDomains` render-time `fail` works; `existingSecret` paths render no Secret; NetworkPolicy
  admits oauth2-proxy when enabled; web `readOnlyRootFilesystem` has its emptyDir mounts.
- **Contract freshness**: `openapi.json` regenerated in the Docker recipe with zero diff; the
  `response_model` ratchet (`test_openapi_contract.py`) still pins the eight untyped endpoints.
- **README ↔ zh-TW mirror**: identical heading structure (14/14), same last-touched commit
  (`0347fc0`), same section set incl. the KM sections and the proxy guide pointers.
- **`.env.example` / `values.yaml` / compose** agree on all sixteen base variables and their
  defaults (spot-checked each); the three proxy variables are documented in all three places.
- **CHANGELOG** release notes carry the four visible KM changes the spec §12 lists.
- **Runner cadence constants** match spec §6.3 exactly (`_HEARTBEAT_S 10`, `_STALE_AFTER_S 60`,
  `_CANCEL_POLL_S 1`, `_RECONCILE_EVERY_S 60`); `build_argv` maps `update` to `--method
  standard|fast` (not `-update`).
- **Retention loop** runs at startup and daily (`main.py:50-62`) and prunes `update_output/` after
  each successful update (`runner_loop.py:151-155`).

## R1 / R2 hand-offs — disposition

| item | disposition |
|---|---|
| R1-02 / R1-03 (question-set creation, tag UI) | **Not deliberate deferrals.** Slice plans name both as UI tasks (`kb-slice1 … Task 9 "Consumes … POST\|DELETE /files/{name}/tags"`, `kb-slice2` Task 7 consumes only `GET` sets) with no "not yet" anywhere; README claims both. Severity stands at P1; R3-23 adds the rest of the set lifecycle to the same wave. |
| R1-19 (`test_run` rows on the jobs page) | Spec decision proposed: keep them off the jobs page by default (`?type=index&type=update`) and give the workbench its own cancel (R3-21); the jobs page then needs paging anyway (R3-10). |
| R1-63 / R1-64 / R1-65 / R1-66 (spec drift) | Confirmed; R3-31 lists the additional stale statements so one errata PR closes all. |
| i18n §5.1 verbatim invariant (unfiled hand-off) | Filed inside R3-31. |
| R1-62 (`ACCESS_TOKEN_MINUTES`/`REFRESH_TOKEN_DAYS`) | Confirmed still undocumented in `.env.example`, `values.yaml`, compose and AGENTS.md; fold into R3-13's ops doc. |
| R1-45 / R1-44 / R1-12 (envelopes, operationIds, response models) | R3-32 (error schema) and R3-10 (paging envelope) join that contract wave. |
| Jobs enqueue/cancel unaudited | Extended by R3-34 with the live trail; spec decision still needed (R2-23). |
| `ActionCard` links the active job to the jobs pane, not its log | Left for R4 (visible-behaviour call); noted in the KM §9.3 matrix row. |
| R1-107 / R1-114 / R1-117 | Contract items; no new information. |
| R2-04 (1 MiB upload cap) | **Reproduced in the quickstart** (2.2 MB → HTML 413); README wording R3-18; Helm twin R3-15. |
| R2-23 / R2-26 / R2-38 (spec decisions) | Recorded in R3-34, R3-31; no new information on R2-26. |
| R2-15 / R2-27 (compose smoke, PID 1) | R3-12 depends on R2-27; R3-27 extends R2-15's smoke job with the overlay. `NOTES.txt`/`values.yaml` body-size mention is R3-15. |

## Hand-offs

**T (triage)**
- Treat R3-01 as the top of the backlog with R1-67 (same fix, P0): the README's headline feature
  is wrong on every project's first index, and R2-34's missing post-run `promote` test is the
  regression net.
- Deploy wave candidates that are each one-line: R3-02, R3-03, R3-15, R3-17, R3-28; plus R3-12
  and R3-16 as compose/helm hygiene; R3-27 gates them.
- Contract wave (regen `openapi.json` + types in one PR): R3-06, R3-07, R3-10, R3-22, R3-32 with
  R1-12/R1-44/R1-45.
- UI dead-end wave: R3-08, R3-09, R3-20, R3-21, R3-23/R1-02, R1-03, R3-24, R3-19.
- Ops/observability wave: R3-04, R3-11, R3-13, R3-14, R3-25, R3-29, R3-30.
- Spec errata PR: R3-26, R3-31 (+ R1-63/64/65/66, R2-38).

**R4 (UX walkthrough)**
- The R4 corpus must be ≥ 3 real documents; expect every one to show `skipped` after the first
  index and every citation unlinked (R3-01) — capture that screen in both languages, it is the
  most important screenshot of the walkthrough.
- Reproduce R3-05: run 31 queries in an hour (or set `QUERY_RATE_LIMIT_PER_HOUR=2` in `.env`)
  and record what the workbench says; query a project with no index from the workbench.
- Reproduce R3-06: a failed index followed by the overview's "Last index" card and the project
  list's date.
- Reproduce R3-19: open the log of a running `standard` job and try to scroll up.
- Reproduce R3-20/R3-21: start a batch run, then go to Jobs and press Start; try to cancel the
  batch from the workbench.
- R3-08/R3-09: look for a way to rename the project or change your password as a normal user.
- Upload a 2 MiB file through the SPA and record what the Documents pane shows for nginx's HTML
  413 (R2-04 hand-off, still open).
- Prerequisite check before starting: `npm run screenshots` is still broken (R1-25); this
  session's stack was torn down, so bring up a fresh one from `main`.

## Notes

- **LLM spend.** One `standard` index of two files plus six queries (one `drift`) against the key
  in `~/.graphrag-p3.key`, authorised in-session; no `update` and no batch run.
- **Compose project name.** The quickstart ran as `-p r3quick` to avoid colliding with any
  existing stack; the README's bare `docker compose up` was not exercised on port collision.
- **Scratchpad artefacts** (`logs/compose-up.log`, `logs/helm-*.yaml`, `logs/openapi-regen.log`,
  `login.json`, `create.json`, `query.json`, `codes_report.txt`) were session-local and are not
  retained; the Intro table and the Quickstart section carry the excerpts this document relies on.
- **Severity calls.** R3-02/R3-03 are P1 rather than P0 because each affects one opt-in deploy
  path and fails loudly at install time; R3-04 is P1 because it silently defeats the probe the spec
  designs around. R3-14 is P2 because `must_change_password` limits the window to "before the
  first login". R3-05 is P2 rather than P3 because the two codes it hides are the ones a normal
  user meets most (rate limit, no index yet).
- **`helm template` with `proxyAuth.enabled` and no ingress** renders `AUTH_MODE=proxy` into the
  api Deployment (R3-17); the chart has no `fail` for it although `values.yaml:117` calls it
  required. Not tested on a live cluster.
- **Node 26 in `frontend/Dockerfile`** built fine here (R3-28 is drift, not breakage).
- **`docker manifest inspect`** ran through the host's Docker credential/proxy config; results
  reflect Docker Hub as seen from this machine on 2026-09-19.
