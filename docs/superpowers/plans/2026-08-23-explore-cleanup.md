# Explore Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the review-adjudicated deferred items from Phase 5 (and the Phase-4 `_prepare` carryover): API honesty for unsupported filters, two UX nits + a missing test, main-bundle vendor split, and a real WebGL visual pass.

**Architecture:** No behavior changes beyond the listed items; everything stays inside existing layers and conventions. Three parallel tasks touch disjoint files (backend / frontend src / build config); visual pass and integration run after they land.

**Tech Stack:** unchanged (FastAPI, React 19 + antd 6, vite/rolldown build).

## Global Constraints

- Same as Phase 5 plan Global Constraints (layers, error contract `{"detail": zh-TW}`, Conventional Commits, English comments, zh-TW only UI strings).
- 422 decision (user-adjudicated 2026-08-23): unsupported filter params → HTTP 422 with zh-TW detail「此資料表不支援該篩選條件」; supported params unchanged. UI already gates these inputs, so no frontend change needed.
- No new env vars, no DB migrations, no dependency additions (vendor split is config-only).
- Fast suites green before commit; slow tests NOT run in this wave (no LLM spend; explore slow test unchanged).

---

### Task 1: Backend — 422 filter guards + `_prepare` extraction

**Files:**
- Modify: `backend/src/graphrag_ui/services/explore.py`, `backend/src/graphrag_ui/api/query_routes.py`
- Test: `backend/tests/test_explore_api.py`, `backend/tests/test_query_api.py`

**Interfaces:**
- Produces: service raises `UnsupportedFilterError` (new RuntimeError subclass) when `type_filter` is passed for a table with `spec.type_filter == False`, or `community` for `spec.community_filter == False`; `api/explore_routes.py` maps it → 422 `此資料表不支援該篩選條件`. `query_routes.py` gains `_prepare_query(db, pid, user) -> Project` (project-or-404 + permission block) used by both handlers.

- [ ] **Step 1: Failing tests** — explore: `GET .../artifacts/relationships?type=x` → 422 + detail; `GET .../artifacts/documents?community=0` → 422; `GET .../artifacts/entities?type=PERSON` stays 200 (existing test already proves). Query: both POST and stream pre-check paths still return 404/403 exactly as before (rely on existing tests; add none unless extraction changes shape).
- [ ] **Step 2: Red → implement** — guard at the TOP of `list_artifacts` (before stale/IO, using the spec from Task-1 registry); `_prepare_query` extraction replacing both duplicated blocks (mirror `explore_routes._allowed` shape).
- [ ] **Step 3: Green** — `uv run pytest tests/test_explore_api.py tests/test_query_api.py -v`; then full fast suite once.
- [ ] **Step 4: Commit** `fix(api): 422 for unsupported artifact filters; extract query pre-check`

### Task 2: Frontend — slider commit-on-release, drawer close on table switch, GraphView stale test

**Files:**
- Modify: `frontend/src/components/GraphView.tsx`, `frontend/src/components/ExplorePanel.tsx`
- Test: `frontend/src/components/__tests__/GraphView.test.tsx`, `__tests__/ExplorePanel.test.tsx`

**Interfaces:** none new.

- [ ] **Step 1: Failing tests** — (a) GraphView: response `{stale: true}` renders the Alert「索引進行中,結果可能不完整」; (b) slider drag (fireEvent key/click per existing slider test idiom) does NOT refetch until release (assert fetch count stable pre/post commit event); (c) ExplorePanel: with detail Drawer open, switching table closes the Drawer and does NOT fetch `.../{newtable}/{hrid}`.
- [ ] **Step 2: Red → implement** — slider `onChange` updates local value only, `onChangeComplete` (antd Slider prop; if the installed antd uses `onAfterChange` keep that name) commits to query key; ExplorePanel `setTable` also `setHrid(null)`.
- [ ] **Step 3: Green** — `npx vitest run` full frontend suite + `npx tsc -b --noEmit`.
- [ ] **Step 4: Commit** `fix(ui): slider commit on release, drawer closes on table switch, graph stale alert test`

### Task 3: Build — antd/react vendor split (config-only)

**Files:**
- Modify: `frontend/vite.config.ts`
- Test: verification by build output only (no unit test)

**Interfaces:** none; runtime behavior identical.

- [ ] **Step 1:** Inspect `node_modules/vite/package.json` version — if rolldown-vite (build warning mentioned `rolldownOptions`), use `build.rolldownOptions.output.advancedChunks` with a `vendor` group (`test: /node_modules[\\/](react|react-dom|antd|rc-[a-z-]+|@ant-design)/`) ; if standard vite, use `build.rollupOptions.output.manualChunks` with an `id.includes("node_modules")` split for the same packages. One mechanism only — no both.
- [ ] **Step 2:** `npm run build` → verify: main `index-*.js` drops well below the 1.26 MB baseline (expect a few hundred kB), vendor chunk appears, `GraphView-*.js` lazy chunk still separate (~204 kB), and total gzipped size does not regress >5%.
- [ ] **Step 3:** `npx vitest run` still green (config must not affect test env) + `npx tsc -b --noEmit`.
- [ ] **Step 4: Commit** `build: split antd/react vendor chunk`

### Task 4: WebGL visual pass (controller-executed, after Tasks 1-3 merge to branch)

- [ ] compose stack up with the persisted indexed project; browser-drive 圖譜 mode (login → 專案 → 探索 → 圖譜): verify canvas renders nodes with community colors, slider/type/search controls visibly filter, screenshot archived to `.superpowers/sdd/2026-08-22-explore/smoke/` (masked).
- [ ] Record pass/fail + screenshot path in cleanup report; any rendering failure becomes a fix item before PR.
