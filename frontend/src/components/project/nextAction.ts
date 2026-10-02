// The ordered check behind the overview's action card (spec §9.3), as a
// pure module next to its component — the repo's methods.ts / ratings.ts
// pattern — because exporting functions from ActionCard.tsx would break
// the react/only-export-components fast-refresh warning ratchet.

// What the ordered check reads of HealthOut. A structural subset (not the
// full ProjectHealth) so the pure tests — and any future caller — can
// hand nextAction partial data; a full ProjectHealth satisfies it.
export interface ActionHealth {
  active_job: { id: string; type: string } | null;
  api_key_missing: boolean;
  artifacts_stale: boolean;
  files: { modified: number; new: number; removed: number; skipped: number; total: number };
  has_baseline: boolean;
  ingest_check: string;
  last_index: { job_id: string } | null;
  last_attempt: { job_id: string; status: string } | null;
  latest_run: { regressions: number } | null;
}

export type NextActionKey =
  | "activeJob" | "noDocuments" | "apiKeyMissing" | "artifactsMissing" | "noBaseline"
  | "removed" | "stale" | "skipped" | "regressions" | "healthy";

export interface NextAction {
  key: NextActionKey;
  severity: "success" | "info" | "warning" | "error";
  // Pane-relative route with filters already applied (spec §9.3); the
  // card prefixes /projects/:id. null = healthy, nothing to link to.
  target: string | null;
}

// The action card is ONE ordered check (spec §9.3):
// 1. a running job outranks everything — all later states describe a
//    snapshot it is about to replace; the link opens its live log. It is
//    info: running is the normal state, not a failure (V-04);
// 2. no documents at all: every project starts here, so it is an info
//    prompt to upload, not an index of nothing (R4-04);
// 3. a key settings.yaml needs is still a placeholder: any index would
//    fail at its first model call (F9-01);
// 4. missing output under an existing baseline outranks everything below
//    because every state under it is read off output that is gone or
//    untrustworthy — missing output is not the absence of a problem;
// 5. no baseline needs a FULL index (an update will not establish one) —
//    info before any index ran, an error once one has and failed;
// 6. removed needs a full index too, which is why it outranks 7;
// 7./8. new+modified / skipped are update-grade problems; skipped links
//    to the run that skipped them, where the evidence is (R4-05);
// 9. regressions are the last non-healthy state;
// 10. otherwise healthy.
export function nextAction(health: ActionHealth): NextAction {
  if (health.active_job) {
    // A test run is off the jobs page (decision D2): the workbench shows it.
    const target = health.active_job.type === "test_run" ? "tests" : `jobs?log=${health.active_job.id}`;
    return { key: "activeJob", severity: "info", target };
  }
  if (health.files.total === 0) {
    return { key: "noDocuments", severity: "info", target: "files" };
  }
  if (health.api_key_missing) {
    return { key: "apiKeyMissing", severity: "warning", target: "settings" };
  }
  if (
    health.has_baseline &&
    (health.ingest_check === "unavailable_not_indexed" || health.artifacts_stale)
  ) {
    return { key: "artifactsMissing", severity: "error", target: "jobs" };
  }
  if (!health.has_baseline) {
    // Any finished attempt, not only a success: without a baseline the
    // newest attempt can only have failed or been cancelled.
    return { key: "noBaseline", severity: health.last_attempt ? "error" : "info", target: "jobs" };
  }
  if (health.files.removed > 0) {
    return { key: "removed", severity: "error", target: "jobs" };
  }
  if (health.files.new + health.files.modified > 0) {
    return { key: "stale", severity: "warning", target: "files?state=new,modified" };
  }
  if (health.files.skipped > 0) {
    return {
      key: "skipped",
      severity: "warning",
      target: health.last_index ? `jobs?log=${health.last_index.job_id}` : "files?state=skipped",
    };
  }
  if ((health.latest_run?.regressions ?? 0) > 0) {
    return { key: "regressions", severity: "warning", target: "tests?regressions=1" };
  }
  return { key: "healthy", severity: "success", target: null };
}
