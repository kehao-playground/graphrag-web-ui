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
    index_job_id: `idx-${i}`,
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
        cell("Q1 保固期多長", { rating: "good" }),
        cell("Q1 保固期多長", { rating: "good" }),
      ],
    },
    {
      // previous run good → newest poor: the regression the filter keeps.
      lineage_id: "L3",
      cells: [
        cell("Q3 退貨流程幾天", { rating: "good" }),
        cell("Q3 退貨流程幾天", { rating: "fair" }),
        cell("Q3 退貨流程幾天", { rating: "good" }),
        cell("Q3 退貨流程幾天", { rating: "poor" }),
      ],
    },
    {
      // run-1 never asked (null) and run-4 was cancelled before asking
      // (unfilled placeholder): both mechanisms of 未執行 in one row.
      lineage_id: "L5",
      cells: [
        null,
        cell("Q5 企業採購窗口", { rating: "fair" }),
        cell("Q5 企業採購窗口", { rating: "fair" }),
        cell("Q5 企業採購窗口", { completed: false }),
      ],
    },
    {
      // Added for run-4 and it errored: an error cell is marked, not an
      // unrated answer.
      lineage_id: "L7",
      cells: [null, null, null, cell("Q7 發票怎麼開", { error: "boom" })],
    },
  ],
};

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
