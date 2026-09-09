// Shared MatrixOut / SetListOut / QuestionListOut fixtures for the
// workbench suite (Task 7): one file so RatingMatrix.test and
// Workbench.test mount the exact same project state. 4 runs oldest first,
// rows keyed by lineage — the shapes mirror types.generated.ts.

const SET_ID = "s1";

function run(i: number) {
  return {
    id: `run-${i}`,
    set_id: SET_ID,
    job_id: `job-${i}`,
    // Numeric so cell labels read "#12 · 09-02" like spec §5.3's column
    // labels; index_job_id is the run's index version anchor.
    index_job_id: `${10 + i}`,
    method: "local",
    workspace_config_revision: null,
    started_at: `2026-09-0${i}T10:00:00Z`,
    finished_at: `2026-09-0${i}T11:00:00Z`,
  };
}

function cell(questionText: string, over: Record<string, unknown> = {}) {
  return { result_id: "res", question_text: questionText, rating: null, error: null, completed: true, ...over };
}

export const MATRIX = {
  runs: [run(1), run(2), run(3), run(4)],
  rows: [
    {
      // Steady good across all four runs: never a regression.
      lineage_id: "L1",
      cells: [
        cell("Q1 保固期多長", { rating: "good" }),
        cell("Q1 保固期多長", { rating: "good" }),
        cell("Q1 保固期多長", { rating: "good", result_id: "res-3a" }),
        cell("Q1 保固期多長", { rating: "good", result_id: "r1" }),
      ],
    },
    {
      // previous run good → newest poor: the regression the filter keeps.
      lineage_id: "L3",
      cells: [
        cell("Q3 退貨流程幾天", { rating: "good" }),
        cell("Q3 退貨流程幾天", { rating: "fair" }),
        cell("Q3 退貨流程幾天", { rating: "good", result_id: "res-3b" }),
        cell("Q3 退貨流程幾天", { rating: "poor", result_id: "r2" }),
      ],
    },
    {
      // run-1 never asked (null) and run-4 was cancelled before asking
      // (unfilled placeholder): the two not-run mechanisms in one row.
      // zh-TW: both cells must render 未執行.
      lineage_id: "L5",
      cells: [
        null,
        cell("Q5 企業採購窗口", { rating: "fair" }),
        cell("Q5 企業採購窗口", { rating: "fair", result_id: "res-3c" }),
        cell("Q5 企業採購窗口", { completed: false, result_id: "r3" }),
      ],
    },
    {
      // Added for run-4 and it errored: an error cell is marked, not an
      // unrated answer.
      lineage_id: "L7",
      cells: [null, null, null, cell("Q7 發票怎麼開", { error: "boom", result_id: "r4" })],
    },
  ],
};

// GET /api/test-runs/run-3/results: the run-3 cells above reference these
// (ordered by position, as the endpoint returns). L7 joined in run-4.
export const RESULTS_RUN3 = {
  results: [
    { id: "res-3a", question_id: "q-1", position: 2, question_text: "Q1 保固期多長",
      answer: "保固一年。", citations: null, timings: null, error: null,
      completed_at: "2026-09-03T10:05:00Z", rating: null },
    { id: "res-3b", question_id: "q-3", position: 4, question_text: "Q3 退貨流程幾天",
      answer: "七天內可退。需附發票。", citations: null, timings: null, error: null,
      completed_at: "2026-09-03T10:06:00Z", rating: null },
    { id: "res-3c", question_id: "q-5", position: 6, question_text: "Q5 企業採購窗口",
      answer: "客服信箱。", citations: null, timings: null, error: null,
      completed_at: "2026-09-03T10:07:00Z", rating: null },
  ],
};

// GET /api/test-runs/run-4/results: r3 is the cancelled placeholder
// (nothing filled in), r4 the errored one — everything the worker owns is
// nullable by construction (spec §5.3).
export const RESULTS_RUN4 = {
  results: [
    { id: "r1", question_id: "q-1", position: 2, question_text: "Q1 保固期多長",
      answer: "保固一年。", citations: null, timings: null, error: null,
      completed_at: "2026-09-04T10:05:00Z", rating: null },
    { id: "r2", question_id: "q-3", position: 4, question_text: "Q3 退貨流程幾天",
      answer: "七天內可退。", citations: null, timings: null, error: null,
      completed_at: "2026-09-04T10:06:00Z", rating: null },
    { id: "r3", question_id: "q-5", position: 6, question_text: "Q5 企業採購窗口",
      answer: null, citations: null, timings: null, error: null,
      completed_at: null, rating: null },
    { id: "r4", question_id: "q-7", position: 8, question_text: "Q7 發票怎麼開",
      answer: null, citations: null, timings: null, error: "boom",
      completed_at: "2026-09-04T10:08:00Z", rating: null },
  ],
};

// The matrix cell aria-label contract (Task 8): question text × column,
// the column named by the run's index version anchor — what selection
// targets and the diff labels echo.
export function cellLabel(questionText: string, runIndex1to4: number): string {
  return `${questionText} × #${10 + runIndex1to4}`;
}

export const SETS = {
  sets: [{ id: SET_ID, name: "客服常問 20 題", created_at: "2026-09-01T00:00:00Z" }],
};

export const QUESTIONS = {
  questions: Array.from({ length: 20 }, (_, i) => ({
    id: `q-${i}`,
    set_id: SET_ID,
    lineage_id: `L${i}`,
    text: `題目 ${i + 1}`,
    position: i + 1,
    created_at: "2026-09-01T00:00:00Z",
  })),
};

// PreflightOut as far as the workbench cares: the active job that blocks
// POST /test-runs (any type — spec §7.3), or null.
export const PREFLIGHT = {
  active_job: null,
  last_run: null,
  cache_bytes: 0,
  cache_quota_mb: 512,
  disk_free_mb: 50000,
  disk_watermark_mb: 2048,
};

export const SET_ID_S1 = SET_ID;
