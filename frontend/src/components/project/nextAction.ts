import type { TFunction } from "i18next";

// The ordered check behind the overview's action card (spec §9.3), as a
// pure module next to its component — the repo's methods.ts / ratings.ts
// pattern — because exporting functions from ActionCard.tsx would break
// the react/only-export-components fast-refresh warning ratchet.

// What the ordered check reads of HealthOut. A structural subset (not the
// full ProjectHealth) so the pure tests — and any future caller — can
// hand nextAction partial data; a full ProjectHealth satisfies it.
export interface ActionHealth {
  active_job: { id: string; type: string } | null;
  artifacts_stale: boolean;
  files: { modified: number; new: number; removed: number; skipped: number };
  has_baseline: boolean;
  ingest_check: string;
  latest_run: { regressions: number } | null;
}

export type NextActionKey =
  | "activeJob" | "artifactsMissing" | "noBaseline" | "removed"
  | "stale" | "skipped" | "regressions" | "healthy";

export interface NextAction {
  key: NextActionKey;
  severity: "success" | "warning" | "error";
  // Pane-relative route with filters already applied (spec §9.3); the
  // card prefixes /projects/:id. null = healthy, nothing to link to.
  target: string | null;
}

// The action card is ONE ordered check (spec §9.3):
// 1. a running job outranks everything — all later states describe a
//    snapshot it is about to replace;
// 2. missing output under an existing baseline outranks everything below
//    because every state under it is read off output that is gone or
//    untrustworthy — missing output is not the absence of a problem;
// 3. no baseline needs a FULL index (an update will not establish one);
// 4. removed needs a full index too, which is why it outranks 5;
// 5./6. new+modified / skipped are update-grade problems;
// 7. regressions are the last non-healthy state;
// 8. otherwise healthy.
export function nextAction(health: ActionHealth): NextAction {
  if (health.active_job) {
    return { key: "activeJob", severity: "error", target: "jobs" };
  }
  if (
    health.has_baseline &&
    (health.ingest_check === "unavailable_not_indexed" || health.artifacts_stale)
  ) {
    return { key: "artifactsMissing", severity: "error", target: "jobs" };
  }
  if (!health.has_baseline) {
    return { key: "noBaseline", severity: "error", target: "jobs" };
  }
  if (health.files.removed > 0) {
    return { key: "removed", severity: "error", target: "jobs" };
  }
  if (health.files.new + health.files.modified > 0) {
    return { key: "stale", severity: "warning", target: "files?state=new,modified" };
  }
  if (health.files.skipped > 0) {
    return { key: "skipped", severity: "warning", target: "files?state=skipped" };
  }
  if ((health.latest_run?.regressions ?? 0) > 0) {
    return { key: "regressions", severity: "warning", target: "tests?regressions=1" };
  }
  return { key: "healthy", severity: "success", target: null };
}

// Job-type vocabulary shared by the action card's active-job copy and the
// overview's last-index mini-card (Workbench keeps its own copy for its
// conflict notice); unknown types render raw, like methodLabel.
export function jobTypeLabel(v: string, t: TFunction): string {
  return v === "index" ? t("workbench.typeIndex")
    : v === "update" ? t("workbench.typeUpdate")
    : v === "test_run" ? t("workbench.typeTestRun")
    : v;
}
