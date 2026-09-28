import type { QueryClient } from "@tanstack/react-query";
import {
  projectFiles, projectHealth, projectJobs, projectTags, testRunMatrix,
} from "./queries";

// The reads a project's writes make stale, fanned out in one place (R1-18)
// so no mutation site has to remember which listings derive from what.

// A file was uploaded or deleted: its listing, the tag catalog, and the
// health aggregate the sidebar badges and overview card read — plus the
// project list's batch health, which counts the same states.
export function invalidateProjectFiles(qc: QueryClient, pid: string) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: projectFiles(pid).queryKey }),
    qc.invalidateQueries({ queryKey: projectTags(pid).queryKey }),
    qc.invalidateQueries({ queryKey: projectHealth(pid).queryKey }),
    qc.invalidateQueries({ queryKey: ["projects", "health-batch"] }),
  ]);
}

// A job started or ended: on top of the file-derived reads (an index moves
// every file's state), the job list, the matrix a test run fills, and the
// explore artifacts an index rewrites. The preflight is left out — the
// caller has just read it.
export function invalidateProject(qc: QueryClient, pid: string) {
  return Promise.all([
    invalidateProjectFiles(qc, pid),
    qc.invalidateQueries({ queryKey: projectJobs(pid).queryKey, exact: true }),
    qc.invalidateQueries({ queryKey: testRunMatrix(pid).queryKey }),
    qc.invalidateQueries({ queryKey: ["projects", pid, "artifacts"] }),
  ]);
}

