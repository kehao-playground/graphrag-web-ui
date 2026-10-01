import type { MatrixRow, TestRun } from "../../api/types";

// Pure rating arithmetic for the matrix (Task 7), mirroring the backend's
// domain/test_runs.py so the filter and the /health overview (slice ③)
// can never disagree.

export type Score = "good" | "fair" | "poor";

// Ordered best to worst; a regression is a move to a HIGHER index. The
// drawer's Segmented and its 1/2/3 shortcuts follow this order too.
export const SCORES: readonly Score[] = ["good", "fair", "poor"];

export const RATING_ORDER: Record<string, number> = Object.fromEntries(
  SCORES.map((score, i) => [score, i]),
);

// Label and tag colour per score: the one place the vocabulary is spelled.
export const RATING_META = {
  good: { labelKey: "workbench.ratingGood", color: "green" },
  fair: { labelKey: "workbench.ratingFair", color: "gold" },
  poor: { labelKey: "workbench.ratingPoor", color: "red" },
} as const satisfies Record<Score, { labelKey: string; color: string }>;

// A lineage regresses when its NEWEST rating is worse than the previous
// run's. A missing rating on either side is not a regression — an unrated
// or not-yet-asked question must not make the filter cry wolf.
// Runs arrive oldest→newest with cells aligned by position, so the last
// two columns are "previous run" and "newest run".
export function regressedLineages(runs: TestRun[], rows: MatrixRow[]): Set<string> {
  if (runs.length < 2) return new Set();
  const previous = runs.length - 2;
  const newest = runs.length - 1;
  const regressed = new Set<string>();
  for (const row of rows) {
    const before = row.cells[previous]?.rating ?? null;
    const now = row.cells[newest]?.rating ?? null;
    if (before === null || now === null) continue;
    if ((RATING_ORDER[now] ?? -1) > (RATING_ORDER[before] ?? -1)) regressed.add(row.lineage_id);
  }
  return regressed;
}
