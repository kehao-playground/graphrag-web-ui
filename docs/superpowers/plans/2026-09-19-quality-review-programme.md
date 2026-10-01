# Quality Review Programme — Multi-Session Plan

> **For agentic workers:** this plan is executed **one session per phase**.
> Each session opens with the kickoff prompt of its phase (§7), reads only
> what that prompt lists, produces the artifact the phase names, updates
> the status table (§8), and commits. Review phases (R1–R4) produce
> **findings only — no code changes**. Fix phases (F*) follow
> superpowers:executing-plans against `backlog.md`, TDD, one PR per wave.

**Goal:** Review GraphRAG Web UI across five dimensions — code quality,
system architecture, functionality, usability, UI/UX — with operations
folded into functionality, rank everything found into one backlog, then
fix it in priority order. All dimensions weigh equally; severity, not
dimension, decides order.

**Why phased:** the codebase is ~9.6k lines of backend across four layers
and ~5k lines of hand-written frontend, with 55 + 26 test files and six
design specs. Any one dimension fits a session; all of them do not, and
fixing before the global ranking exists means small fixes get overturned
by later refactors.

**Tech stack:** FastAPI + pydantic v2, SQLAlchemy 2 async, Alembic,
duckdb over parquet, graphrag 3.1.2 (CLI subprocess + env-shielded
`graphrag.api`), React 19 + TS + antd 6 + react-router 7 + TanStack Query
+ i18next, vitest, openapi-typescript codegen, docker compose + Helm.

## 1. Global Constraints

- **Review sessions do not change code.** They may run tools (linters,
  tests, `/code-review`, `/security-review`, a live stack) and write their
  findings document. If a session finds a one-line fix, it records it as
  a finding with effort `S`; the fix waits for its wave.
- **One finding format** (§4) in every review document, so triage merges
  them mechanically.
- **Evidence or it did not happen.** Every finding cites `file:line`, a
  command and its output, or a screenshot path under
  `docs/superpowers/reviews/assets/`. "Feels slow" is not a finding;
  "query drawer stays blank for 4 s before the first SSE frame, see
  `r4-08.png`" is.
- **Read the specs first, judge against them.** `docs/superpowers/specs/`
  is authoritative for intended behaviour. A deviation from a spec is a
  finding; a spec that is itself wrong is also a finding (category
  `spec`).
- **Intel Mac host** (`uname -m` = `x86_64`): `uv sync` fails on lancedb.
  Backend gates run in Docker with the exact command in `AGENTS.md`
  "Commands" (named volumes for the venv and uv cache; docker.sock
  mounted for testcontainers). Frontend gates run natively (Node 24).
- **Fix waves obey AGENTS.md** without exception: layering, alembic-only
  schema changes, fixed env-var names, contract gate (`openapi.json` +
  `types.generated.ts` in the same commit), English comments, both
  locales per UI string, README ↔ `docs/zh-TW/README.md` in the same PR,
  Conventional Commits. Every wave ends green on every gate listed in
  AGENTS.md "Commands".
- **Git flow:** branch from `main`, `gh pr create`, `gh pr merge --rebase
  --auto --delete-branch`. Never a merge commit.
- **Documents in English.** Findings, backlog and this plan are English
  (CONTRIBUTING.md). Screenshots are taken in both UI languages where the
  copy matters.

## 2. Phases

| # | Session | Produces | Depends on |
|---|---|---|---|
| R1 | Architecture & code quality | `docs/superpowers/reviews/2026-MM-DD-r1-architecture.md` | — |
| R2 | Correctness, security & tests | `…-r2-correctness-security.md` | R1 (its map of hot spots) |
| R3 | Functionality & operations | `…-r3-functionality-ops.md` | — (reads specs) |
| R4 | Usability & UI/UX, live walkthrough | `…-r4-ux.md` + `reviews/assets/r4-*.png` | R3 (its spec-vs-shipped list becomes the walkthrough script) |
| T | Triage | `docs/superpowers/reviews/backlog.md` — ranked, cut into waves, **approved by the user** | R1–R4 |
| F1…Fn | Fix waves | one PR each; `backlog.md` rows flipped to done | T |
| V | Verify & close | re-run R4 script, regen screenshots/openapi/types, README + CHANGELOG, status table complete | all F* |

R1 → R2 → R3 → R4 is the intended order; R3 may run before R2 if that
is more convenient, but R4 must follow R3 and R2 should follow R1.

## 3. Phase Briefs

### R1 — Architecture & code quality

**Question answered:** does the code match the architecture it claims,
and is it in a shape that the fix waves can work in safely?

Backend (`backend/src/graphrag_ui/`):
- Layering compliance against AGENTS.md rules — `domain/` free of I/O and
  external imports; `services/` free of FastAPI and `HTTPException`;
  graphrag imports only in `adapters/index_runner.py`, `adapters/workspace.py`
  and `adapters/graphrag_search.py`; duckdb only in adapters. Confirm with
  `grep` and `codegraph explore`, not by reading the docs.
- Transaction boundaries: every service that does `flush → external work
  → commit` rolls back on failure; `audit()` never commits.
- Error translation: service errors reach the client as the documented
  problem codes (`project_indexing`, `job_conflict`, …); no bare 500s
  on expected failures.
- Unit size and cohesion: `services/files.py` (758 lines),
  `services/test_runs.py`, `api/files_routes.py`, `adapters/models.py`
  — what responsibilities are mixed, what a split would look like.
- Duplication across services and routes; naming consistency; dead code.
- `mypy`/`ruff` state and any `# type: ignore` / `noqa` that hides a
  real problem.
- Dependency health: `pip-audit` output (the advisory CI `audit` job is
  currently red — say why and whether it matters), pins vs. ranges.

Frontend (`frontend/src/`):
- Component structure: pages vs. panels vs. `components/project`,
  `components/files`, `components/tests`; prop drilling vs. query hooks;
  where server state lives (TanStack Query) vs. local state.
- API layer: `api/` client vs. `types.generated.ts` — hand-maintained
  types (`Citation`) and their drift risk.
- i18n: every user-visible string keyed; both locales complete
  (`i18n/locales/{zh-TW,en-US}.ts` line counts differ by 5 — find the
  gap); zh-TW copy uses Taiwan terminology and full-width punctuation.
- `oxlint --max-warnings=6` ratchet: what the 6 warnings are.
- Bundle: explore graph chunk is lazy — confirm; anything else that
  should be.

Tools: `ruff`, `mypy`, `oxlint`, `tsc`, `codegraph explore`,
`/code-review high` on the whole tree (diff target `main` against the
initial commit if needed), `/simplify` in read-only mode (report, do not
apply).

### R2 — Correctness, security & tests

**Question answered:** where does the system do the wrong thing, and
where is nothing stopping it from doing so?

- Auth & RBAC: permission atoms (`domain/permissions.py`) vs. every
  route's check — table of route × required atom × actual check.
  Built-in role ids and seeds. Owner immutability. JIT provisioning in
  proxy mode; `PROXY_ADMIN_EMAILS` grant semantics; special-use domain
  rejection.
- Session security: JWT lifetimes, refresh rotation, logout semantics,
  `must_change_password` gate; proxy-secret constant-time compare.
- Input handling: upload filename/path safety, size and quota
  enforcement (`413`), `settings.yaml` write validation, `.env` key
  whitelist and masking on read-back, query length bounds, SSE injection.
- Concurrency: project freeze (`409 project_indexing`) races with
  in-flight uploads (`test_project_freeze.py` covers some — what is not
  covered); one-active-job-per-project vs. `MAX_CONCURRENT_JOBS`; job
  cancellation; process cleanup on API restart.
- SSE: error mid-stream, client disconnect, nginx buffering, timeouts.
- Data lifecycle: log retention, `UPDATE_OUTPUT_KEEP_LATEST`, cache and
  disk watermarks — are they enforced, and what happens at the limit.
- Audit completeness: every state-changing route writes an audit row.
- Tests: coverage gaps by module (run `pytest --cov` in the Docker
  recipe; `vitest --coverage`), slow tests' value, flaky candidates,
  what CI does not gate (e.g. `docker compose build`, helm on proxy
  values).

Tools: `/security-review` (on the full tree), `/code-review high`,
coverage runs, targeted reading of `services/auth.py`, `services/roles.py`,
`services/files.py`, `api/deps.py`, `api/errors.py`, `adapters/graphrag_search.py`.

### R3 — Functionality & operations

**Question answered:** does the product do what the specs promise, and
can someone run it for a team without reading the source?

- Spec-vs-shipped matrix: for every section of each spec in
  `docs/superpowers/specs/` — shipped / partial / missing / diverged, with
  the file that implements it. This matrix is R4's walkthrough script.
- Feature dead ends: actions that lead nowhere (buttons without
  follow-up, states with no exit, e.g. `removed` documents that need a
  full rebuild — is the rebuild offered where the state is shown?).
- API contract: `openapi.json` vs. routes (regen and diff), error
  responses documented, pagination and limits consistent.
- Error-message quality: every problem code the backend can emit vs.
  what the SPA shows for it in both languages.
- Operations: compose and Helm upgrade path (migrations on startup,
  what happens on a failed migration), backup/restore of Postgres +
  workspaces, resource limits, logging (what an operator sees when a job
  fails), health endpoints, secrets handling in both deploy paths,
  `NOTES.txt` accuracy, `.env.example` and `values.yaml` completeness.
- Documentation vs. behaviour: README quickstart actually works from a
  clean clone (do it); troubleshooting section still accurate.

Tools: reading, `docker compose config`, `helm template` with several
value sets, a clean-clone quickstart run.

### R4 — Usability & UI/UX (live walkthrough)

**Question answered:** what does a knowledge manager experience, and
where does the interface get in their way?

Prerequisites (check before starting; stop and say so if missing):
- Docker running; `.env` configured; a `GRAPHRAG_API_KEY` that can run
  a real index; a small real corpus (10–30 documents, `text`) in a known
  directory. `standard` method for the first index (README caveat).
- Chrome with the Claude extension, or Playwright (`frontend/scripts/
  capture-screens.mjs` shows the existing harness).

Script (both languages, screenshots to `reviews/assets/r4-NN-<step>.png`):
1. First login → forced password change → empty projects list.
2. Create project → Documents tab empty state → upload (single, bulk,
   over-limit file, wrong extension) → tags, filters, preview.
3. Settings → env key set/mask → settings.yaml edit → save during a
   running job (expect 409 — how is it shown?).
4. Index (standard) → live log → completion → Documents states
   (`indexed`, then modify one → `modified`, delete one → `removed`).
5. Query in all four modes → citations → click to preview at passage →
   removed-document citation → mid-answer error (kill the key).
6. Tests: save a question set, run it, rate with keyboard, regressions
   filter, diff two runs, edit a question with runs.
7. Explore tables and graph view with the real output.
8. Admin: users, roles, audit — as `user_admin`, then as a project
   `viewer` (what is hidden vs. what 403s).
9. Overview action card and project-list health flags after each state
   change above.

Evaluate against: Nielsen's heuristics (visibility of status, match to
the user's language, user control, consistency, error prevention,
recognition over recall, flexibility, minimalism, error recovery, help);
empty / loading / error / success states for every panel; keyboard
navigation and focus order; colour contrast (antd tokens) and labels for
screen readers; layout at 1280 px and 1920 px; wait times with no
feedback; copy consistency between languages and with the README's
terms; antd usage consistency (Drawer vs. Modal, Table density,
notification vs. message).

### T — Triage

- Concatenate all findings into `backlog.md` with the §4 columns plus
  `wave`.
- Rank: severity first, then effort ascending inside a severity, then
  cluster by area so one wave touches one region of the code.
- Propose waves. Default shape (adjust to what was found):
  F1 P0/P1 correctness + security; F2 architecture refactors that later
  waves depend on; F3 functionality gaps and ops; F4 UX; F5 leftovers
  worth doing. Each wave must be finishable in one session — split if
  not.
- Present waves to the user; **the user approves before F1 starts**.
  Findings the user declines get `wave: won't-fix` with a one-line
  reason, never deleted.

### F1…Fn — Fix waves

- Kickoff reads `backlog.md`, filters its wave, and works top-down.
- TDD per finding (failing test first where the finding is behaviour;
  for pure refactors, the existing tests are the net — run them before
  and after).
- One PR per wave; PR body lists the backlog ids fixed. On merge, flip
  the rows to `done` with the PR number.
- Anything discovered mid-wave that is not in the backlog is added as a
  new row (`Fx-NN`), not fixed on the spot, unless it blocks the wave.

### V — Verify & close

- Re-run the R4 script end to end; new screenshots replace the README
  set via `npm run screenshots`.
- Regenerate `openapi.json` and `types.generated.ts`; confirm CI green.
- Update README (both languages), CHANGELOG (`Unreleased` → dated
  entry), AGENTS.md test counts.
- Mark the status table complete; note anything left in the backlog as
  deliberately deferred.

## 4. Finding Format

Every review document has a short intro (scope, tools run, commands and
their exit status) followed by one table:

| id | severity | effort | area | finding | evidence | recommendation |
|---|---|---|---|---|---|---|
| R1-01 | P1 | M | backend/services | `files.py` mixes upload, snapshot and preview responsibilities | `services/files.py:1-758` | split into `files.py` / `snapshots.py` / `preview.py` along the three call graphs |

- **id**: `<phase>-<NN>`, stable once written — the backlog and PRs
  reference it.
- **severity**: `P0` broken/insecure in a way a user or attacker hits;
  `P1` wrong or missing, must fix before calling the product done;
  `P2` should fix, degrades quality or maintainability; `P3` nice to
  have.
- **effort**: `S` < 1 h, `M` half a session, `L` a full session or
  more (an `L` is a candidate to become its own wave or spec).
- **area**: path prefix (`backend/services`, `frontend/pages`, `deploy`,
  `docs`, `spec`).
- **evidence**: `file:line`, a command + output excerpt, or a screenshot
  path.
- **recommendation**: what to change, concretely enough that a fix
  session does not need to re-investigate.

Findings that are observations without a recommended change (e.g. "this
is fine, checked") go in a separate "Verified OK" list so the next
reviewer does not repeat the check.

## 5. Prerequisites Checklist

- [ ] Docker running; `.env` with a strong `JWT_SECRET` and routable
      bootstrap admin email.
- [ ] `GRAPHRAG_API_KEY` valid for indexing (R4, V only).
- [x] Small real corpus for R4/V in a known path — committed at
      `docs/superpowers/reviews/assets/r4-corpus/` (15 documents, 2026-09-21).
- [x] Backend Docker gate recipe works on this host — verified
      2026-09-19: the command in AGENTS.md "Commands" (run from the repo
      root) passed ruff, ruff format, mypy and 533 fast tests in 7 m 25 s;
      cold `uv sync` 75 s, warm ~1 s via the named volumes.
- [ ] `gh auth status` OK (fix waves).

## 6. File Structure

```
docs/superpowers/
  plans/2026-09-19-quality-review-programme.md   # this file; §8 is the live status
  reviews/
    2026-MM-DD-r1-architecture.md
    2026-MM-DD-r2-correctness-security.md
    2026-MM-DD-r3-functionality-ops.md
    2026-MM-DD-r4-ux.md
    backlog.md                                   # T; updated by every F* and V
    assets/r4-NN-<step>.png                      # R4 and V screenshots
```

## 7. Session Kickoff Prompts

Copy the block for the phase into a fresh session. Each assumes the
session starts in the repo root on `main`, up to date.

**R1**
```
Execute phase R1 of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§1 constraints, §3 R1 brief, §4 format), AGENTS.md, and the
specs index in docs/superpowers/specs/. Produce
docs/superpowers/reviews/<today>-r1-architecture.md. Findings only, no
code changes. Run the backend gates with the Docker command in AGENTS.md
(Intel Mac). When done, update §8 of the plan, commit on
a branch, open a PR, rebase-merge it.
```

**R2**
```
Execute phase R2 of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§1, §3 R2, §4), AGENTS.md, and the R1 review doc's
findings table (for hot spots). Run /security-review and /code-review
high on the full tree, plus coverage in the Docker recipe. Produce
docs/superpowers/reviews/<today>-r2-correctness-security.md. Findings
only, no code changes. Update §8, PR, rebase-merge.
```

**R3**
```
Execute phase R3 of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§1, §3 R3, §4), then every spec in docs/superpowers/specs/.
Build the spec-vs-shipped matrix first, then the ops review, then run the
README quickstart from a clean clone. Produce
docs/superpowers/reviews/<today>-r3-functionality-ops.md. Findings only.
Update §8, PR, rebase-merge.
```

**R4**
```
Execute phase R4 of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§1, §3 R4 incl. prerequisites, §4) and the R3 doc's
spec-vs-shipped matrix. Check the prerequisites and stop if any is
missing. Bring the compose stack up, walk the script in both languages
with screenshots into docs/superpowers/reviews/assets/, and produce
docs/superpowers/reviews/<today>-r4-ux.md. Findings only. Record the
corpus path used. Update §8, PR, rebase-merge.
```

**T**
```
Execute phase T of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§3 T, §4) and all four review docs. Produce
docs/superpowers/reviews/backlog.md ranked and cut into waves, then
present the waves to me for approval before committing. After approval:
update §8, PR, rebase-merge.
```

**F\<n\>**
```
Execute fix wave F<n> of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§1, §3 F*), AGENTS.md, and docs/superpowers/reviews/backlog.md
filtered to wave F<n>. Use superpowers:executing-plans; TDD per finding;
all AGENTS.md gates green (backend in Docker). One PR titled
"<type>(<scope>): fix wave F<n> — <theme>" listing the backlog ids. On
merge, flip those rows to done with the PR number and update §8.
```

**V**
```
Execute phase V of docs/superpowers/plans/2026-09-19-quality-review-programme.md.
Read the plan (§3 V) and backlog.md. Re-run the R4 script, regenerate
screenshots, openapi.json and types, update README (both languages),
CHANGELOG and AGENTS.md counts. Mark §8 complete. PR, rebase-merge.
```

## 8. Status

| phase | status | date | artifact / PR | notes |
|---|---|---|---|---|
| plan | done | 2026-09-19 | this file | |
| R1 | done | 2026-09-19 | `reviews/2026-09-19-r1-architecture.md` (PR #28, addendum PR #29) | 118 findings (P1 4, P2 48, P3 66) incl. `/code-review high` + `/simplify` addendum; all gates green; CI `audit` job red on transitive nltk advisories |
| R2 | done | 2026-09-19 | `reviews/2026-09-19-r2-correctness-security.md` | 39 findings (P0 3, P1 2, P2 10, P3 24) + 17 R1 rows dispositioned; P0s are graphrag config loading (`${VAR}` from the API environ, workspace `.env` merged into `os.environ`, unconfined storage `base_dir`); backend coverage 95% with greenlet tracing, frontend 82% lines |
| R3 | done | 2026-09-19 | `reviews/2026-09-19-r3-functionality-ops.md` | 36 findings (P0 1, P1 3, P2 14, P3 18) + spec-vs-shipped matrix for all six specs; clean-clone quickstart passed all ten steps via the API incl. one real `standard` index, which reproduced R1-67 on the happy path (every file `skipped`, `source_name` null — R3-01 P0); proxy compose overlay broken (`web:80`), default helm postgres image unpullable, `/api/ready` always 200 (R3-02/03/04 P1) |
| R4 | done | 2026-09-21 | `reviews/2026-09-21-r4-ux.md` + `reviews/assets/r4-*.png` (165) + `reviews/assets/r4-corpus/` | 42 findings (P1 6, P2 22, P3 14) from a full nine-step walkthrough in both languages on a 15-document real corpus (one `standard` index, 2 m 19 s); P1s are a false "modified by someone else" modal on any 409 (R4-01), Form mode showing empty model fields (R4-02), the retrieval page dead-ending without a question set (R4-03), the overview telling new projects to index nothing / to re-index after R3-01 (R4-04/05), and citations linking only on the SSE route (R4-40); 18 R1–R3 hand-offs dispositioned with screenshots |
| T | done | 2026-09-21 | `reviews/backlog.md` | 235 rows (P0 5, P1 14, P2 94, P3 122) cut into 36 one-session waves, approved 2026-09-21 as proposed; R1-67 raised to P0 (= R3-01); 20 same-fix pairs share a wave; R1-15 and R1-36 won't-fix; decisions D1–D8 recorded in the backlog header; P0/P1 all closed by F12, F32–F36 are P3-only |
| F1 | done | 2026-09-22 | PR #34 | Config trust boundary — R2-01, R2-02, R2-06, R4-42 closed: `adapters/workspace_env.py` is the one boundary (workspace `.env` the only `${VAR}` source in-process and in the CLI subprocess, `set_cwd=False` with paths anchored to the workspace, allowlisted child env, reserved `.env` keys refused, referenced keys undeletable); 569 fast + 5 paid slow tests green; two pre-existing test defects filed as F1-01/F1-02 under F35. **Ops action pending:** rotate `JWT_SECRET` in every deployment that has had non-admin users |
| F2 | done | 2026-09-22 | PR #36 | First-index truth and citation linking — R1-67/R3-01, R2-03, R4-40 closed: `promote()` recovers the baseline's title set from the post-run `documents.parquet` (first index no longer reads all-`skipped`); `domain/settings_confinement.py` keeps storage/reporting/cache `base_dir`s, the lancedb `db_uri` and prompt paths inside the workspace and pins `input.type`, enforced at settings write, enqueue, dry-run and in-process config load (400 `settings_path_escape` / `settings_input_locked`); the non-stream query path maps citations through the cached parquet frames so POST and stored batch results link like the stream. 609 fast + 2 paid slow tests green; `input.file_pattern` deliberately left editable (see backlog F2 note) |
| F3 | done | 2026-09-22 | PR #38 | Deploy P1s, upload cap, readiness — R3-02, R3-03, R2-04, R3-15, R3-18, R3-04, R2-22, R3-14, R3-17, R3-28 closed: overlay upstream `web:8080` (CI checks it against nginx's `listen`); bundled postgres on the frozen but pullable `bitnamilegacy/postgresql:16.6.0-debian-12-r2` (CI `docker manifest inspect`s the default render); nginx `client_max_body_size 1g` + ingress `proxy-body-size <uploadMaxFileMb+1>m`, 2 MiB upload verified through the built image; `/api/ready` → 503 with a `ReadyOut` model and `OSError` caught; placeholder/short `BOOTSTRAP_ADMIN_PASSWORD` refused at startup; Helm `fail` guards for proxy-auth without ingress/host and an English `NOTES.txt` that branches on proxy mode; web image on `node:24-alpine`. 619 fast tests green; preflight/launch-UI half of R3-04 filed as F3-01 under F10 |
| F4 | done | 2026-09-23 | PR #46 | Job state machine — R1-01, R1-77, R1-90, R2-09, R2-11, R2-12 closed: `jobs_repo.finish` writes only while the row is queued/running (a worker returning after reconcile is discarded, nothing promoted) and survives a failing promotion by re-writing the terminal state with the failure in `error`; a queued cancel finishes the job on the spot and `_execute` finishes a cancel flagged before spawn (no snapshot, epoch bump or spawn); `_cancel_poll` suppresses `ProcessLookupError`; every terminal job write stamps its test run's `finished_at`; `delete_project` locks, answers 409 `job_conflict` while any job is active, commits before an off-loop `rmtree` whose failure is logged. 632 fast tests green; main spec §5 amended |
| F5 | done | 2026-09-24 | PR #48 | Write-path races and input mutation — R1-74, R1-04, R1-05, R1-06, R1-92, R2-28, R2-21 closed: `services/project_lock.input_mutation()` (lock → freeze re-check → rows → flush → `InputMutation.apply(<fs step>)` → commit, rollback on failure) carries all eight lock+commit write sites (tags with `freeze=False`); the settings hash is re-checked inside the lock and rows flush before `settings.yaml` is written; `.env` is read inside the lock; the upload quota is measured inside the lock with in-flight `.tmp-` files excluded; bulk delete runs one savepoint per file and reports `failed` names (contract + panel toast, knowledge-manager spec amended); `add_question` takes the lock and `live_questions` breaks position ties by `created_at, id`. 642 fast tests green (10 new race tests in `test_write_races.py`), 171 vitest |
| F6 | done | 2026-09-24 | PR #50 | Sessions and auth — R2-05, R1-68, R2-10, R2-26, R1-83, R1-55, R1-116, R2-31 closed: `rotate_refresh` consumes the token with one conditional `UPDATE … RETURNING` and commits the revoke with the successor; a token re-presented within 30 s while its successor is unused gets that same successor (derived as `HMAC(JWT_SECRET, token)`), otherwise reuse revokes all of the user's tokens; `refresh_tokens.family_id`/`family_created_at` (alembic `b7e2c4a91d30`) cap a login's chain at 30 days (decision D4, spec §8.4 amended in zh-TW). SPA refresh runs under a `navigator.locks` lock and retries with a token another tab stored; only a 401 ends the session (5xx/429 throw `RefreshUnavailableError`, `restore()` retries 1/2/4 s); proxy logout arms the redirect once-guard; change-password goes through `api()` with `try/finally`. 650 fast backend + 179 vitest green. R1-83 unit-tested only — no IdP for the live overlay; new row F6-01 sits in F25 |
| F7 | done | 2026-09-28 | PR #52 | Frontend substrate — R1-81, R1-16, R1-22, R1-109, R1-20 closed, plus R1-21 (an F9 row that is the same fix): `client.ts` exports `apiJson<T>`/`sendOk` throwing `ApiRequestError {status, body}`, every hand-written `api()`+`detailOf` site is migrated (`detailOf` deleted; the quiet health queries and Login keep `api()`); SettingsPanel throws in `mutationFn` and reads the 409 body in `onError` (R4-01 left for F9); `fallbackKey` typed `ParseKeys`; `sseUrl()` owns the `?token=` rule; `api/types.ts` aliases every inline response literal; `ErrorBoundary` at the Layout outlet (reset on route change) and around the lazy GraphView (Retry builds a fresh `lazy()`). Component tests stub `fetch` (`testing/stubFetch.ts`) so the real client runs under every panel test. 189 vitest, lint at 6; backend untouched — one timing flake in the Docker gate filed as F7-01 under F35 |
| F8 | done | 2026-09-28 | PR #54 | Frontend substrate — query factories and client defaults — R1-17, R1-49, R1-87 closed: `api/queries.ts` holds every server read as a `queryOptions()` factory (no inline key or read path left in components; invalidation by `factory(...).queryKey`); `api/queryClient.ts` `createQueryClient()` (App and every component test) sets `retry: false` and raises one error toast from `QueryCache`/`MutationCache` with a typed `meta.silent` opt-out, replacing 19 toast effects, 18 `onError` toasts and 27 `retry: false` copies; health/batch-health/files fresh for 30 s with no focus refetch; `components/labels.ts` owns `jobTypeLabel`/`jobTypeShortLabel` (now `test_run`)/`roleLabel`. 198 vitest, lint at 6; backend untouched. The 30 s staleness after a job finishes is handed to R1-18 in F10 (backlog F8 note) |
| F9 | done | 2026-09-28 | PR #55 | Settings pane — R4-01, R4-02, R1-82, R4-22, R4-23, R4-24, R4-35, R3-24 closed (R1-21 already in #52): only `settings_conflict` opens the conflict modal, while `project_indexing` shows a toast, keeps the draft and freezes the pane from the shared preflight (`components/project/frozen.ts`, also used by FilesPanel); form mode walks dotted paths and saves chunk sizes as integers; dirty tracking with Save disabled when clean, `useBlocker` + `beforeunload` (App moved to `createBrowserRouter`); `EnvKeyOut.is_placeholder` (contract regen) drives a "Not set" tag and an alert, the key section moved above the YAML, translated headers, Popconfirm on delete; dry-run renamed "Check settings (offline)" with an honest success text; save 400s shown inline with line/column and Go-to-line (no gutter); Settings entry gated on `project:view`. 651 fast backend + 209 vitest, lint at 6. The overview-card half of R4-23 filed as F9-01 under F10 |
| F10 | done | 2026-09-28 | PR #57 | Overview and jobs state — R4-04, R4-05, R1-18, R1-84, R4-06, R1-19, R3-20, R4-25, R4-26, R1-110, F3-01, F9-01 closed: the action card gains a leading info "upload documents" rule and an API-key rule (`HealthOut.api_key_missing`: a key `settings.yaml` references is absent or a placeholder), no-baseline is info until an index has run, the skipped card names what to check and links to the last run's log, the running-job card opens its log (`jobs?log=`; a test run goes to the workbench) — spec §9.3 amended and renumbered; `api/invalidate.ts` fans out file/job invalidation and `useActiveJobWatch` in the project layout polls the preflight while a job runs and refreshes files/tags/health/jobs/matrix/artifacts on every active-job change (fixes the workbench banner too); the jobs page asks `?type=index&type=update` (backend `type` filter now repeatable, D2), translates statuses, disables Start with a notice while a job runs or when `PreflightOut.graphrag` is `not-installed`, names count/method/billing in the confirm, ticks elapsed time, titles the log drawer, `isPending` spinners. 654 fast backend + 227 vitest, lint at 6. Workflow/batch progress (rest of R4-26) stays with R3-36/R3-07 |
| F11 | done | 2026-09-28 | PR #59 | Question-set lifecycle and the retrieval landing — R1-02, R3-23, R4-03 closed: `PATCH /question-sets/{sid}` renames a live set (`question_set.renamed`, contract regen), blank set names are 422; the matrix picker row creates/renames/archives sets and the questions list adds/archives questions (a question with runs says its answers stay), all gated on `project:edit_content`; a project without sets lands on the ad-hoc query, the matrix shows a *Create question set* empty state, the launch button reads *Run the set* before any run; *Save as question* can create a set inline; questions numbered in ask order. Spec §8/§9.2 and README (both languages) amended. 659 fast backend + 237 vitest, lint at 6; walked live in both languages on the compose stack. The optional pane rename in R4-03 was not taken |
| F12 | done | 2026-09-28 | PR #61 | Documents pane — R1-03, R4-07, R4-08, R1-112, R2-13, R4-31 closed: *Tag selected…* dialog adds/removes tags over the selection and a per-row *Edit tags* link saves only the difference (writes 4 at a time, failures named, open while frozen — tags are metadata); one keyed progress toast and one summary per upload drop with a single listing refresh; `FileListOut.max_file_bytes` (contract regen) names the cap in the dragger hint and refuses larger files before sending; row delete prunes the selection and the bulk buttons count rows that still exist; the ingest-check banner waits for `has_baseline`. Spec §9.1 amended. 659 fast backend + 247 vitest, lint at 6; walked live in both languages on the compose stack. English plural forms filed as F12-01 under F19 |
| F13 | done | 2026-09-29 | PR #63 | Backend API substrate — R1-10, R1-11, R1-30, R1-27, R1-32, R1-46, R1-80, R1-88 closed: `services.errors.CodedServiceError` (`code`, `params`) is the base of every client-visible service error, `*NotFoundError` names with a `LookupError` base, builtin contracts replaced by typed errors (`InputFileNotFoundError`, `EnvKeyNotFoundError`, `MemberNotFoundError`) so a stray `KeyError`/`FileNotFoundError` is a 500; `api/errors.SERVICE_ERROR_STATUS` maps each class to its status once through one app-level handler (route `except` ladders gone; `IntegrityError`, the settings 409 body and dry-run stay local); `api/deps.require_project(atom)` (`ProjectView`, `ProjectEditContent`, …) replaces 30 inline preambles and three helpers, projects routes use `{pid}`; `can()` drops `is_active`; 422s are `{detail: [{type, loc, msg}], code: validation_failed}` with `input`/`ctx` stripped, env PATCH takes `EnvKeyIn` (three `env_*` body codes retired). Contract regen, i18n + RBAC specs amended. 667 fast backend + 247 vitest, lint at 6 |
| F14 | done | 2026-09-29 | PR #65 | Contract — fields and response models — R1-12, R1-43, R2-07, R2-08, R2-16, R3-06, R3-07, R3-22 closed: `QueryOut`/`ArtifactPageOut`/`ArtifactDetailOut`/`GraphOut`/`CancelOut`/`LivenessOut` on the seven untyped routes (ratchet −6), `types.ts` hand-written Citation/QueryTimings/Artifact*/Graph* now generated aliases (SSE frames reuse `CitationOut`/`QueryTimingsOut`), graph node `type` is `""` when absent; one `UuidStr` replaces ten validators (wire type unchanged); failed test-run questions store a fixed `query failed` (alembic data migration `d41c7e2b9a15` scrubs older rows; drawer shows translated copy); `display_name` 1–100, `query` ≤ 2000 / `response_type` ≤ 100 on POST and stream; health `last_index` = newest succeeded, new `last_attempt` (any status) on per-project and batch payloads — overview shows a non-succeeded attempt in red, list flags a failed one, no-baseline card keys on it; `JobOut.progress` in the workbench notice ("3 of 20 questions answered"); POST/PATCH `/projects` return `my_permissions`. Contract regen, knowledge-manager spec amended. 673 fast backend + 251 vitest, lint at 6 |
| F15 | done | 2026-09-29 | PR #67 | Contract — envelopes, paging, error schema — R1-45, R3-10, R3-32 closed: jobs list and settings versions page with `limit` (1–200, default 50) / `offset` and answer `{items, total}` (`JobPageOut`, `VersionPageOut`; shared `PageLimit`/`PageOffset`), the silent 50-row caps gone, jobs ordered by `queued_at, id` so pages never overlap; the jobs table and settings history paginate server-side (20 per page, `keepPreviousData`); decision D1 recorded in the main spec (bare-array lists and audit/artifacts `rows` migrate when their callers are next touched); `api/openapi.py` documents `4XX` → `ApiErrorOut` and every 422 → `ValidationErrorOut` (replacing `HTTPValidationError`) on every operation, settings PUT 409 → `SettingsConflictOut`; `ApiErrorBody` is a generated alias, i18n spec §4.1 amended. Contract regen. 677 fast backend + 253 vitest, lint at 6 |
| F16 | done | 2026-09-29 | PR #69 | Answers and citations — R3-05, R4-09, R4-10, R4-11, R4-13, R4-14 closed: pre-stream query refusals (`query_rate_limited`, `not_indexed`, `query_config_failed`, `query_failed`) answer the stream as 200 `text/event-stream` with one `event: error` `{detail, code}` frame so the SPA localizes them (auth/validation stay plain HTTP; i18n spec §4.3 amended, no contract change); answers render through a small text-built Markdown renderer (no HTML passthrough) whose `[Data: …]` markers anchor into the citations panel, bounded and auto-following only while streaming (follow pauses on scroll-up); citations list each document once with one-line expandable excerpts and each entity/relationship/report once, linked to Explore via `?table=&row=` (new tab); ad-hoc query gains a per-method duration hint, elapsed counter and *Cancel*; the preview marks and scrolls to the passage under one plain header line and wraps with `overflow-wrap: anywhere`. Knowledge-manager spec §9.2/§9.3 amended. 677 fast backend + 273 vitest, lint at 6. Not walked live — the compose stack's admin password was changed during R4 and is not recorded |
| F17 | done | 2026-09-29 | PR #71 | Admin, members and access — R3-08, R3-09, R4-16, R4-17, R4-18, R4-37, R1-56, R3-35 closed: the members pane owns its reads (members, users, role catalog) and the layout context keeps only the project and its atoms; *Edit project info* (`PATCH /api/projects/{pid}`, `project:manage`); add-member defaults to `viewer`, a role change granting `project:edit_settings`/`project:manage` confirms first, adds and changes toast; *Change password* in the sidebar menu (local mode) shares one dialog with the forced first-login change and signs in again afterwards, since the backend revokes every refresh token on a change (the forced flow used to lapse when its access token expired); audit page with catalog action labels and filter, named targets, key: value payloads in a fixed-layout table and an error state; roles page speaks the catalog (names, descriptions, scope tags) with no actions on built-ins; `RequirePermission` guards `/admin/*` and a refused/unknown project renders the same 403/404 page with *Back to projects*; health batches chunked by 200 ids with a "health unavailable" cell. RBAC spec §8 and README (both languages) amended; oxlint ratchet 6 → 3. 677 fast backend + 290 vitest. Not walked live (compose admin password still unrecorded) |
| F18 | done | 2026-09-29 | PR #74 | Explore — R4-15, R4-39, R1-86, R1-52, R1-51, R2-33, R4-12 closed: an un-indexed project shows the catalogued `not_indexed` sentence with *Go to jobs* in table and graph mode (`ArtifactQueryError`; Explore reads are `meta.silent`, errors render in place); the level Select shows the server's level, a community legend (top 8 + other + no community), node click opens the entity drawer, zoom in/out/reset controls, labels only above 12 px rendered size; search only re-highlights (no clear, re-seed or FA2 re-run); a failed detail fetch shows an Alert in the drawer; `columnLabel` falls back to the raw column (env headers already done in F9); page sizes 10/20/50/100. The ReadPixels warning is sigma 3's pointer picking and stays. Main spec Explore bullet amended; oxlint ratchet 3 → 2. 303 vitest; backend untouched. One `ProjectDetail` find got a 5 s timeout as a stop-gap for the pre-existing load flake F1-01 (still open, F35). Not walked live (compose admin password still unrecorded) |
| F19 | done | 2026-09-29 | PR #76 | Copy and terminology — R1-23, R4-20, R1-66, R4-19, R3-33, R1-24, R4-34, R1-57, R1-59, F12-01 closed: zh-TW full-width punctuation throughout; i18n spec §8 glossary (job = 任務, document = 文件 vs. on-disk 檔案, token = 權杖, workspace = 工作區, environment variable = 環境變數) with the seven 作業 strings and the English loanwords gone; en-US sentence case, `project_indexing` interpolates a localized `job_type` (client-side via the job label), the broken English active-job and last-index sentences fixed; labels without spec refs or config keys, "Answered in N s" with the stage breakdown in a tooltip, Explore hash ids collapsed to a count/*Show* toggle; three emitted codes catalogued and `user_last_admin_protected` removed, with `backend/tests/test_error_catalog.py` diffing raise-site codes against the catalog both ways; two dead keys deleted, slug label translated; counted messages on i18next plurals (en-US `Shape` requires `_one`/`_other`). Catalog lints in `i18n.test.ts` (punctuation, job word/loanwords, sentence case, plurals, unused keys); CONTRIBUTING UI-copy checklist. 680 fast backend + 312 vitest, lint at 2. Slug stays visible (not behind a Details toggle). Not walked live (compose admin password still unrecorded) |
| F20 | done | 2026-09-29 | PR #78 | Visual polish and dates — R4-21, R1-54, R4-33, R1-58, R4-36, R4-30, R4-32, R4-38 closed: `src/theme.ts` raises secondary/tertiary text to 0.65 alpha (5.7 : 1, contrast-tested); `i18n/format.ts` `formatDateTime` (numeric, ~165 px, one line in every date column) / `formatShortDateTime` (matrix and diff run labels) replace every `toLocaleString`, the raw ISO in version history, the `MM-DD` slices and Explore's graphrag `creation_date`, cache warning via `humanBytes`; `<html lang>`/title follow i18next's `languageChanged` (so `/login` too), `index.html` `lang="en"`, shared `LanguageSelect` on the login card; empty states with a sentence (projects list → *Create the first project*, documents empty vs. filtered, version history, filtered graph); sized one-line tags header, nowrap 120 px Explore detail labels, flexing stat tiles; identity line via `common.nameWithEmail`, neutral row Delete link, gold pending badge, and a removed row no longer offers Delete (its test was vacuous under antd's CJK button spacing). i18n spec §5.3 amended. 330 vitest, lint at 2; backend untouched. **Walked live** in both languages on a fresh compose stack (`down -v`); the walk caught the spelled-out date style wrapping in zh-TW (fixed in the same PR). The stack is served on 18080 because `kubefwd` holds 8080 (new row F20-01 under F25); the admin password is the `# WALK_ADMIN_PASSWORD=` comment in `.env` |
| F21 | done | 2026-09-29 | PR #82 | Listing and health hot path — R1-69, R1-70, R1-71, R1-96, R1-94 closed: `project_files.mtime_ns` (alembic `9c5e1f7a2b64`) makes the row the listing's hash cache — a file whose size and `st_mtime_ns` match its row is not re-hashed, a file modified less than 2 s before the scan is never cached (racy-clean rule), refreshed hashes are written back lock-free in one executemany, `add_tags` reuses the row hash; discovery locks and commits only when a name is missing; `files.file_listings(projects)` reads project_files, baselines and entries once per batch and `batch_health` finds last index/attempt with one `DISTINCT ON` each, so the query count no longer grows with the number of projects; `files.scan_input` is the one `input/` walk (listing and start snapshot); `list_projects(ids=)` owns the visibility rule. Input byte total deliberately left on `usage_bytes` (same walk as the quota). Knowledge-manager spec §5.1 amended. 690 fast backend tests; frontend and contract untouched |
| F22 | done | 2026-09-29 | PR #84 | Query path and event-loop blocking — R1-72, R1-73, R2-19, R2-20, R1-89, R1-97, R1-98 closed: citations flatten only the rows the answer cites and map only cited hrids to documents (vectorized pandas filters, no per-query copy of every frame); `domain/citations.py` folds every label and frame name onto one canonical key (`frame_key`, `cited_ids`), so the service-side synonym fan-out is gone; `run_query`/`stream_query` share `_preamble` and `_citations`; a run-scoped `CitationMemo` carries titles and the baseline row, so a batch reads the baseline once (the G0/G1 guard still withholds links); the duckdb documents read, `load_config` and argon2 run in worker threads, `load_config` is memoised per `(inode, mtime_ns, size)` of `settings.yaml`/`.env` (2 s racy window, failures never cached), and frame loads are gathered; `prune_update_output` runs off the loop at both callers; upload chunk writes go through `to_thread`; the index subprocess writes its log file directly and a failed job reads only the tail. `hash_password`/`verify_password` are async. 703 fast backend tests; contract and frontend untouched. The optional `to_thread` layering assertion was not added |
| F23 | done | 2026-09-29 | PR #86 | `services/files.py` split — R1-07, R1-29, R1-93 closed: the 894-line module is five — `input_scan.py` (leaf: `CHUNK_BYTES`, `sha256_file`, `ScannedFile`, `cached_scans`, `scan_input`; named for the scan rather than `file_hash` because `index_snapshots` imports the scan since F21, and the deferred import is gone), `file_listing.py` (`list_files`, `file_listings`, row write-back), `file_preview.py` (preview window, locator, `LocatorMismatchError`, limits), `file_tags.py`, and `files.py` (349 lines: whitelist, errors, quota, upload, delete); `files.input_file()` is the one validate/join/exists sequence (preview, tags, bulk delete); `delete_file` is `bulk_delete`'s single-name case and still raises on a failed unlink (new test). 704 fast backend tests; contract and frontend untouched |
| F24 | done | 2026-09-29 | PR #88 | Runtime ops and observability — R3-11, R3-12, R2-27, R3-29, R2-23, R3-34, R2-25 closed: `main.configure_logging()` puts the root logger at INFO with timestamp and level (decision D7, no `LOG_LEVEL`; httpx/LiteLLM/in-process graphrag quieted) and the job lifecycle is logged — enqueued, claimed, started, spawned pid, cancel requested, finished with status/exit/duration, reconciled jobs (WARNING), update_output pruning, the retention sweep totals, bootstrap admin creation; every state-changing route audits (decision D3): `job.enqueued`, `job.cancelled`, `user.password_changed` (own change moved into `services.auth.change_password`), both locales, and `test_audit_catalog.py` diffs `audit()` call sites against `AUDIT_ACTIONS` both ways; the SSE `?token=` is `[redacted]` in the nginx access log (custom `log_format`) and in uvicorn's access log (logger filter) (decision D5); api `CMD` `exec uvicorn`; compose `restart: unless-stopped` everywhere, api `init`, `stop_grace_period: 120s`, `mem_limit: 2g`, `/api/health` healthcheck with web waiting for healthy; Helm `startupProbe` (5 s × 60). Main spec §5/§8.1/§8.2/§8.4/§10 amended. 719 fast backend + 330 vitest, lint at 2. **Walked live** on the compose stack (18080 overlay): api healthy, PID 1 `docker-init`, `stop` in 0.8 s with lifespan shutdown, 0 token hits in web/api logs. New rows F24-01 (nginx error log still carries the token on upstream failure) and F24-02 (cancel audit in a second transaction) under F33, F24-03 (healthcheck access-log noise) under F25 |
| F25 | done | 2026-09-30 | PR #90 | Ops docs and deploy knobs — R3-13, R3-25, R3-16, R3-30, R1-62, F6-01, F24-03, F20-01 closed, plus F25-01/F25-02 found mid-wave: README *Operations* (both languages; upgrade, backup/restore — compose round trip verified on a throwaway stack — logs, sizing, session lifetimes), NOTES/values point at it; enqueue refuses an over-quota project with 409 `quota_exceeded`, preflight `usage_bytes`/`project_quota_mb`, `POST /projects/{pid}/cache:clear` (`project:run_jobs`, 409 while a job is active, `cache.cleared` audit) with a *Clear cache* button that says the next index re-bills (contract regen); ingress sends `/api` through the web Service in both modes (CI guard); `persistence.storageClassName`/`existingClaim` (CI guard), one release per namespace in NOTES, image build-and-push in the README; `ACCESS_TOKEN_MINUTES`/`REFRESH_TOKEN_DAYS` in AGENTS.md, `.env.example`, compose and Helm; successful probes dropped from the access log; taken-host-port troubleshooting. F6-01 **walked live** against a new test IdP (`deploy/test-idp`: Dex + a socat forwarder owning oauth2-proxy's network namespace), which found F25-01 (a Docker client proxy breaks the overlay's upstream hop → `NO_PROXY=web` on auth) and F25-02 (proxy logout landed on the local sign-in form via a heuristically cached `index.html` and a 401 on `/api/auth/config` read as local mode → `restore()` treats it as proxy mode, nginx sends `no-cache` on HTML); screenshots `reviews/assets/f25-0*.jpg`. Main spec §8.2/§8.4/§10 amended. 726 fast backend + 334 vitest, lint at 2 |
| F26 | done | 2026-09-30 | PR #92 | CI gates, dependencies and coverage config — R2-15, R3-27, R1-14, R2-14, R2-37, R1-13, R1-48, R2-39, R1-25 closed, plus F26-01 found mid-wave: new `smoke` job (`.github/scripts/compose-smoke.sh`, bash 3.2-safe) boots the compose stack, signs in, changes the bootstrap password, creates a project, uploads 2 MiB through the web nginx (fails with 413 when `client_max_body_size` is removed) and reads `/api/ready`, then boots the proxy overlay against the test IdP and expects 401 on `/api/auth/me`; the helm job asserts 1/3/2 Ingresses for the disabled/bundled/external proxy-auth value sets with `auth-signin` on the app Ingress only; nltk 3.9.4 → 3.10.3 via `[tool.uv] override-dependencies` (decision D8), pip-audit down from 30 advisories to one (`PYSEC-2026-3740`, no fixed release, not reachable; dated `--ignore-vuln`), `audit` job required, policy in AGENTS.md; `pandas`/`starlette` declared; `tests/test_layering.py` (AST walk, plus a test that every rule fires); greenlet coverage config + `pytest-cov` (fast suite 97%); `capture-screens.mjs` reads labels from the locale catalogs and navigates by URL (ran end to end in both languages; CI `node --check`s it). F26-01: the api image never shipped readable NLTK corpora — `nltk.download` writes to `/root/nltk_data` whatever `NLTK_DATA` says, and nltk 3.10 refuses proxied fetches while `quiet=True` hid it — the Dockerfile now downloads into `$NLTK_DATA`, fails on any miss and opts into proxied fetches for that `RUN` only; the fast-method noun-phrase path verified offline as the runtime user on 3.10.3. No slow test covers the fast method (it fails on tiny corpora), so no paid run was made. 728 fast backend + 334 vitest (F1-01 flake 1 of 3), lint at 2; every CI job green incl. `smoke`. New row F26-02 (sentence-chunking corpora not baked) under F33 |
| F27 | done | 2026-09-30 | PR #95 | Services cohesion — R1-08, R1-26, R1-31, R1-75, R1-33, R1-34, R1-95, R1-101 closed. `services/test_runs.py` is now three modules: `test_runs.py` (enqueue + read models), `test_run_worker.py` (`execute_test_run`, config revision) and `ratings.py`. `query.prepare_query`/`execute_query` are public. `ACTIVE_STATUSES`/`FREEZING_JOB_TYPES` live in `domain/jobs.py`, and `project_lock.active_job(types=)` is the one active-job query (`freezing_job`, `citations.read_generation`). New `services/fs_stats.py` (`tree_bytes`, `workspaces_free_bytes`) serves quota, cache, preflight and `/api/ready`. `referenced_key_missing` reads the `.env` through `read_workspace_env`. `roles.manager_holders_stmt`/`has_active_manager` back the role guard, the user guard and the bootstrap probe. Renames: `jobs.get_job`/`list_jobs`, `jobs_repo.last_succeeded`, `roles.list_roles_for_user`. `ExploreReadError.tail` and the no-op role filter are gone. R1-101: five of six nits taken; the JIT-grant move was declined because it would race on the `user_roles` PK (backlog F27 note). 728 fast backend tests; contract and frontend untouched |
| F28 | done | 2026-09-30 | PR #97 | Job log viewer and progress — R3-36, R1-85, R3-19, R2-32 closed, plus F28-01 found mid-wave. The runner's watch loop reads `stats.json` on every heartbeat into `jobs.progress = {done, total}` and writes only when the count moves. `total` is the pinned graphrag pipeline length (index 10, update 18; `domain/jobs.PIPELINE_WORKFLOWS`, diffed against `PipelineFactory` in a test) or the length of a `settings.yaml` `workflows:` override. A stats file older than the run is ignored, both while running and in the final `jobs.stats`. The jobs table shows "N of M workflows done" on running rows. `JobLogViewer` appends one text node per animation frame. Scrolling up pauses auto-follow, and the drawer header has Follow / Pause auto-scroll. The `error` listener shows "reconnecting" during native retries; a refused retry shows a warning with Reconnect, which refreshes the token and resumes at `?offset=`. Virtual scrolling was dropped from spec §7 (§6.3/§7 amended). oxlint ratchet 2 → 1. Contract regen (docstring). F28-01: a new `brace-expansion` advisory turned the required `audit` job red; lockfile lifted to 2.1.7 in the same PR. 735 fast backend + 341 vitest, all CI jobs green. **Checked live**: a two-file `standard` index on the compose stack ticked 0 → 1 → 3 → 9 of 10; SSE `?offset=` resume confirmed. The browser drawer was not walked (sign-in needs the user) |
| F29 | done | 2026-09-30 | PR #99 | Workbench polish — R3-21, R4-27, R4-28, R4-29, R4-41, R1-111, R2-30, R1-113 closed: the running test run's banner has *Cancel* (the jobs pane's endpoint and confirm copy; only for `test_run`, hidden once requested); runs are named by method + localized start time (`methods.runLabel`) in the matrix column, the diff sides and the drawer title, with the index anchor as a tooltip; the whole matrix `td` is the click/keyboard target; keyboard rating has a ref-based in-flight guard; the drawer focuses on mount through a callback ref (the lost R4 key went to the matrix cell while results loaded), and an early key waits on `ensureQueryData` and rates once results land; *Press Esc to close* on the result drawer, the preview drawer and the diff modal; `open` derived from its target in the question editor, AdminRoles, RunDiff (`pair`) and JobLogViewer. R4-29 had already been fixed by F11. Knowledge-manager spec §5.3/§9.2 amended. 350 vitest, lint at 1; backend and contract untouched; all CI jobs green. Not walked live |
| F30 | done | 2026-10-01 | PR #101 | `files_routes` and api cleanups — R1-09, R1-76, R1-42, R1-104, R1-105, R1-106 closed, plus F30-01 found mid-wave: the upload-size guard is pure ASGI (`api/middleware.UploadSizeGuard`), the files models live in `api/schemas.py`; `services.errors.pipeline_step()` is the one log-and-wrap for query/explore steps; one coded `NotIndexedError` replaces the adapter errors in `api/`, and rate limit / unknown table / unsupported filter render through the app-level table; dry-run, job-log tailing, global perms and init failure go through services, and `test_layering` holds `api/` to `adapters.models`/`adapters.db` (+ the initializer seam); `_principal_from_token` is the one token gate for header and `?token=`; `set_member` returns its role; the must-change middleware is deleted (unrouted path → 404; i18n and proxy-auth specs amended). F30-01: new pyjwt/urllib3 advisories turned `audit` red, lockfile lifted to 2.15.1/2.8.0. 741 fast backend tests; contract and frontend untouched; all CI jobs green. New rows F30-02 under F33, F30-03 (member-add toast test flake) under F35 |
| F31 | done | 2026-10-01 | PR #104 | Artifacts, log tail and runner efficiency — R1-78, R1-102, R1-79, R2-18, R1-99, R1-100, R1-103, R2-29 closed, plus F31-01 found mid-wave: the graph cap, community lookup and edge filter run in duckdb (Python holds only kept nodes and their edges); `list_rows` is one statement (`COUNT(*) OVER ()`) on a cursor of one shared in-memory duckdb database; the log tail reads ≤ 256 KiB per step off the loop, cuts chunks on UTF-8 boundaries (resumable offsets), asks `finished()` only when idle and at most every 5 s, and drains after the end; `_reload` only refreshes an instance the session holds and the heartbeat `RETURNING`s the cancel flag — the 1 s cancel poll stays (user's call, spec §6.3); the log sweep filters in SQL with a pure `log_path_for`; frame cache stats once and keeps sizes; proxy identity is two SELECTs and no commit for a known user, reconcile still on every resolve. F31-01: CVE-2026-84377 on litellm 1.92.0 (pinned by graphrag-llm) turned `audit` red — `override-dependencies` lifts it to 1.92.2. Main spec §6.1/§6.3/Explore amended. 764 fast backend tests; contract and frontend untouched; all CI jobs green |
| F32 | done | 2026-10-01 | PR #105 | Contract — naming and ids — R1-44, R1-107, R1-108, R1-117, R1-114 closed: operationIds are the handler names (`generate_unique_id_function` → `route.name`) and every handler is `<verb>_<resource>` (`list_projects`, `update_role`, `stream_job_logs`), the rule pinned by `test_openapi_contract`, with route modules whose names would shadow a service importing it as a module (hygiene spec A5.2 amended); the matrix `?runs=` is capped at 50; settings version models validate from the ORM row; `ProjectOut` carries `owner_email`/`owner_display_name` and the projects page no longer fetches `/api/users`; `GET /api/artifact-tables` serves the domain registry, Explore reads columns and filter flags from it (`ArtifactTableName` generated, pinned to `TABLES` by a test) and keeps only the labels (main spec Explore and KM spec §9.2 amended). One contract regen. 768 fast backend + 352 vitest, lint at 1. The F30-03 member-toast flake (F35) also shows up on `main` under load |
| F33 | done | 2026-10-01 | PR #106 | Adapters and service hardening — R1-35, R1-37, R1-38, R1-39, R1-47, R1-91, R1-28, R2-17, R2-24, F24-01, F24-02, F26-02, F30-02 closed. `request_cancel` no longer commits, so `jobs.cancel` writes the cancellation and its audit row in one commit; the runner-loop writes still commit their own transactions, an exception now recorded in AGENTS.md (R1-35 took its "document it" branch). One parquet table map; dead `SearchAdapter`, role-id sets and the inert `heartbeat`/`pid` parameters removed, `jobs.pid` dropped (alembic `e5a2d9c4f170`). Docstrings now cite spec sections instead of plan tasks. A concurrent same-name create retries with a suffixed slug, and a failed init removes its workspace directory. Control characters in file names are `file_name_unsafe`. A hand-removed `settings.yaml` reads as empty with hash `""`, a save puts it back, and job start refuses with `settings_missing` (a 404 would have been a dead end). SSE routes take a 60 s path-bound `?ticket=` from `POST /api/auth/sse-ticket` and refuse `?token=`; the ticket is stateless and reusable within its minute so native EventSource retries still work, and log redaction covers `ticket=` (main spec §8.4 amended). The image bakes graphrag's chunking corpora readable by uid 10001. The members query moved into the service, and create returns the owner atoms. Contract regen. 779 fast backend + 354 vitest, lint at 1, all CI jobs green. **Checked live** on the compose stack: the migration applied, a ticket streams through nginx, the old token is refused, and no credential reached the logs. Paid slow tests were adapted but not run |
| F34 | todo | | | Frontend structure leftovers |
| F35 | todo | | | Test gaps |
| F36 | todo | | | Spec errata |
| V | todo | | | |

Statuses: `todo` · `in progress` · `done` · `skipped (reason)`. A
session that ends without finishing its phase writes `in progress` and
a one-line "resume from" note so the next session can pick it up.
