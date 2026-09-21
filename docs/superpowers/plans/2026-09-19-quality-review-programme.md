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
| T | todo | | | |
| F1 | todo | | | wave themes set at T |
| F2 | todo | | | |
| F3 | todo | | | |
| V | todo | | | |

Statuses: `todo` · `in progress` · `done` · `skipped (reason)`. A
session that ends without finishing its phase writes `in progress` and
a one-line "resume from" note so the next session can pick it up.
