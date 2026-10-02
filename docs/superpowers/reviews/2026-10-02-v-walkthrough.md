# V — Verify walkthrough (2026-10-02)

## Intro

**Scope.** Phase V's first step: the R4 nine-step script walked again, end to end, on a fresh
compose stack built from `main@e47196b` (after F1–F36). Both UI languages, 1280 × 800 with 1920
× 1080 captures of the project list and the graph. The walk had two purposes: confirm that the
fixes for the R4 findings hold on the real product, and find what is still wrong. The later V
steps (README, CHANGELOG, closing §8) wait for wave F37, which fixes the rows found here (the
user's call, 2026-10-02).

**Corpus.** The R4 corpus, `docs/superpowers/reviews/assets/r4-corpus/` (15 documents). Indexed
once with `standard` (`gpt-4.1` + `text-embedding-3-large`): 2 m 23 s, all 15 documents
`indexed`.

**Tools run.** Playwright chromium (`frontend/node_modules`, headless, `locale` and
`Asia/Taipei` pinned per run), driven by session-local scripts that sign in through the login
form. A separate compose project, `docker compose -p v` on host port 28080 (8080 is held by
kubefwd), keeps the walk off the regular stack and gives step 1 a real first sign-in. `curl` and
the API filled in what the screenshots cannot show. Screenshots are in
`docs/superpowers/reviews/assets/v-NN-<step>-{zh,en}[-1920].png` (141 files, 14 MB).

**No code was changed in this step.** The README screenshots were regenerated with `npm run
screenshots` (12 files under `docs/assets/screenshots/`).

### Commands and exit status

| command | exit | result |
|---|---|---|
| `docker compose -p v -f docker-compose.yml -f <28080 override> up -d --build` | 0 | `/api/ready` → `{"db":"ok","graphrag":"3.1.2","disk_ok":true}` |
| `npm run screenshots` (BASE_URL=:18080) | 0 | 12 captures, both locales |
| step 1: wrong password, first sign-in with forced change (short, then valid), admin-created user's forced change | — | as designed (`v-01-*`) |
| step 2: create project, upload 1 + 14 files, `.csv`, a 51 MiB `.txt`, tags over a selection, tag filter, preview | — | 51 MiB refused client-side with the cap named; `.csv` refused with the accepted types (`v-02-*`); **V-01**, **V-02** |
| step 3: bad env key name, real key (masked), form mode edit + save, invalid YAML, leave guard, offline check | — | all as designed (`v-03-*`) |
| step 4: `standard` index via UI, log drawer, settings and documents during the job, settings `PUT` during the job, modify one + delete one | — | `succeeded` 148 s; `PUT` → 409 `project_indexing`; settings and documents frozen with a notice; **V-03**, **V-04**, **V-05** |
| step 5: four modes (local 10.4 s, global 14.4 s, basic 5.4 s, drift 92.8 s), citations, preview at passage, removed-document citation, invalid key | — | `<mark>` at the passage; removed source greyed with a tooltip; **V-06** |
| step 6: question set created in the UI, three questions, two `basic` runs, keyboard rating, regressions filter, compare, edit a question with runs | — | all work; **V-07**, **V-08**, **V-09** |
| step 7: graph, node click, six tables, row detail, un-indexed project | — | `not_indexed` empty state with *Go to jobs*; **V-10** |
| step 8: members (add defaults to viewer), audit, roles; `user_admin`; project `viewer` incl. admin URL and a foreign project | — | hidden vs 403 correct; **V-11** |
| step 9: overview and project list after each change | — | the ladder picks the right card at every step |

## R4 findings re-checked

Every R4 P1 and the R4 rows a user meets on the script were checked in the running product:

| R4 row | now |
|---|---|
| R4-01 false "modified by someone else" on any 409 | settings `PUT` during the job → toast + frozen pane (`v-04-settings-frozen-zh.png`) |
| R4-02 form mode shows empty model fields | model, embedding and chunking fields filled (`v-03-form-mode-*.png`) |
| R4-03 retrieval page dead-ends without a set | lands on the ad-hoc query; *Create question set* in the matrix (`v-05-tests-landing-*.png`) |
| R4-04 / R4-05 overview says "index nothing" / "re-index" | info card for uploads, API-key card, healthy after the first index (`v-02-overview-uploaded-*`, `v-04-overview-indexed-zh.png`) |
| R4-40 citations link only on the stream | links on the stream and in stored batch results (`v-06-drawer-zh.png`) |
| R3-01 / R1-67 first index marks every file `skipped` | all 15 `indexed` (`v-04-files-indexed-zh.png`) |
| R4-42 key rotation ignored until restart | the next query after `PATCH /env` fails (`v-05-badkey-*`) |
| R4-08 413 shown as "upload failed (413)" | refused before sending, cap named (`v-02-files-upload-toolarge-*`) |
| R4-13 preview never shows the match | passage marked and scrolled, one header line (`v-05-citation-preview-*`) |
| R4-15 un-indexed Explore says "No data" | `not_indexed` sentence + *Go to jobs* (`v-07-unindexed-*`) |
| R4-16 add-member defaults to editor | defaults to viewer (`v-08-members-add-default-zh.png`) |
| R4-17 viewer's audit 403 reads as "no entries" | 403 page with *Back to projects* (`v-08-viewer-admin-url-en.png`) |
| R4-22 unsaved settings lost silently | dirty marker + leave guard (`v-03-leave-guard-zh.png`) |
| R4-24 dry-run "passes" with a fake key | "Check settings (offline)" says the model is not called (`v-03-dryrun-result-zh.png`) |
| R4-06 run banner persists after the run | gone when the run finishes; *Cancel run* and "0 of 3 answered" while it runs (`v-06-running-1-zh.png`) |

## Findings

| id | severity | effort | area | finding | evidence | recommendation |
|---|---|---|---|---|---|---|
| V-01 | P3 | S | frontend/components | In the *Tag selected…* dialog, pressing Enter after typing a tag leaves the tags dropdown open over the footer, so *Add to 3 documents* and *Remove from…* are hidden until the user clicks elsewhere in the dialog. | `v-02-files-tag-dropdown-covers-actions-zh.png`; `components/files/TagsModal.tsx:120-130` (`mode="tags"`, `autoFocus`) | Close the dropdown on select (`open` controlled, or `onSelect` → blur), or place the actions above the select. |
| V-02 | P3 | S | frontend/components | The upload hint joins the accepted extensions without a space: "Accepts .txt,.md only" / 「僅接受 .txt,.md」. | `v-02-files-bulk-done-en.png`; `components/files/UploadArea.tsx:116` (`accept` is the input's comma list) | Format the list for display (`accept.split(",").join(", ")`, or `、` in zh-TW) and keep the raw value for the input. |
| V-03 | P3 | S | backend/services | A succeeded index job keeps `progress = {done: 9, total: 10}` although its final `stats` lists all 10 workflows: progress is written only on heartbeats, and the last workflow finished after the last one. The UI shows progress on running rows only, but API readers see a finished job that never reached 10. | `GET /api/jobs/{id}` → `succeeded {"done":9,"total":10}`, 10 keys in `stats.workflows`; `services/runner_loop.py:131-139` | On a succeeded finish, write `progress` from the final stats (or `done = total`) in the same `finish` transaction. |
| V-04 | P3 | S | frontend/components | While an index runs, the overview's action card renders as an **error** (red ✖ "Job running"). A normal running job is not an error; the red competes with real failures on the same card. | `v-04-overview-running-zh.png`; `components/project/nextAction.ts:54` (`severity: "error"`) | Use `info` for the active-job card (spec §9.3 names no severity for rule 1); record it in the spec. |
| V-05 | P3 | S | frontend/pages | The overview shows the last index time twice: the *Last index* stat tile and the *Last index* card ("Index, finished …") below it. In zh-TW the two labels even differ (上次索引 / 最近一次索引) for the same value. | `v-09-overview-final-*.png`; `pages/ProjectOverview.tsx:53-54,69-73` | Keep one: drop the tile, or make the card show what the tile cannot (duration, document count, a link to the log). |
| V-06 | P2 | S | frontend/components | A failed query (here: an invalid API key) shows only a transient "Query failed" toast. After it fades the answer area is empty, with no error state, and nothing points at the likely cause (key or model settings). The user is left with a blank page and no next step. | `v-05-badkey-inflight-zh.png` (toast at 2.5 s), `v-05-badkey-zh.png` (blank afterwards); `components/tests/AdhocQuery.tsx:83,122` (`message.error` only) | Render the failure inline in the answer area (antd `Alert`, kept until the next run) with the catalogued message and, for `query_failed` / `query_config_failed`, a hint and link: "Check the model settings and API key in Settings". |
| V-07 | P3 | S | frontend/components | The launch dialog names the method by its raw id: "Run 3 questions with the "basic" method" / 「將以「basic」方法執行 3 題」. Its code comment says the matrix columns show the id. Since F29 they show the localized `runLabel` ("Basic · Oct 2, 11:53 AM"), so the dialog now disagrees with the matrix. | `v-06-launch-1-zh.png`; `components/tests/LaunchDialog.tsx:51-56` | Pass `methodLabel(method, t)` and update the comment. |
| V-08 | P3 | S | frontend/components | Two runs started within the same minute get identical names ("Basic · Oct 2, 11:53 AM") in the matrix headers, the compare modal and the cells' aria-labels, so neither a sighted user nor a screen reader can tell them apart. Running the set twice in a row hits this. | `v-06-matrix-run2-zh.png`, `v-06-compare-en.png`; cell aria-labels from step 6 (six labels, three distinct); `components/tests/methods.ts:28-29` (`formatShortDateTime`, minute precision) | Disambiguate equal labels: append seconds when two visible runs share the minute, or a run ordinal (#1, #2) to the label. |
| V-09 | P3 | S | frontend/components | The compare modal diffs the raw answer text, so users see Markdown syntax (`### Grace Hopper …`, `[Data: Sources (12)]`) that the result drawer renders. | `v-06-compare-en.png`; `components/tests/RunDiff.tsx:40-46` (`pre-wrap` paragraphs of the raw text) | Diff on the rendered sentences: strip heading/emphasis markers and the citation markers before `sentenceDiff`, or render each side through `Markdown` with the diff marks applied per block. |
| V-10 | P3 | S | spec | The graph defaults to the finest community level (spec §6.1: `MAX(level)`). On the 15-document corpus that level covers about 15 % of the nodes: the legend reads "No community (236)", and most of the graph is grey. Level 0 colours every node. | `v-07-graph-en-1920.png`; `adapters/artifacts.py:104-109`; spec `2026-08-19` §6.1 graph bullet | Default to the level that assigns the most entities, ties to the coarser level (one `COUNT` per level in the same duckdb query), and amend §6.1. |
| V-11 | P3 | S | frontend/pages | Opening a project the user cannot see renders the 403 page and also raises an error toast ("Forbidden" / 「無權限」) from the global query-error handler. The toast says the same thing again, on a page whose only job is to say it. | `v-08-viewer-foreign-project-en.png`, `v-08-ua-foreign-project-en.png`; `api/queries.ts:42-45` (`projectById` has no `meta.silent`), `pages/ProjectDetail.tsx:54,64` | Mark `projectById` `meta: { silent: true }` (ProjectDetail renders every error in place) and add a test that the 403 page raises no toast. |

## Verified OK

- First sign-in: wrong password message, forced change with an 8-character rule that cannot be
  skipped; admin-created users are forced too.
- Project list empty state with *Create the first project*; health flags after each change
  ("Deleted documents still in the index", "1 to index").
- Uploads: per-file toast, one summary for 14 files, wrong extension and over-cap refusals
  named; the removed row offers no Delete and explains itself in a tooltip.
- Settings: placeholder alert, masked key, form-mode values, dirty marker, inline YAML error
  with line/column and *Go to line*, leave guard, the offline-check wording.
- Jobs: confirm names count, method and billing; log drawer titled with type, method and
  start; Start disabled with a notice while a job runs; viewer sees Start disabled.
- Query: method hints, elapsed counter, *Answered in N s*, Markdown answers with anchored
  citation markers, preview at the passage.
- Workbench: empty-set launch refused (Start disabled at 0 questions), *Cancel run* and
  per-question progress, 1/2/3 rating advances, regressions filter, edit-with-runs warning.
- Explore: legend, zoom controls, node click → entity drawer, `not_indexed` empty state.
- RBAC: viewer sees no edit affordances; admin URLs and foreign projects render the 403 page.
- No console errors other than the requests the script made fail on purpose (401 wrong
  password, 400 bad key / invalid YAML, 403/409 on refused pages).
