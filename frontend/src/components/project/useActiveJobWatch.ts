import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { invalidateProject } from "../../api/invalidate";
import { jobsPreflight } from "../../api/queries";

// The project layout's one watch on the job slot (R1-18, R1-84): while a
// job holds the project the shared preflight polls, and every change of
// the active job's id — a job starting, ending, or one replacing another —
// refreshes everything a job changes. Panes never detect job completion
// themselves; they read the refreshed queries.
export function useActiveJobWatch(pid: string, enabled = true) {
  const qc = useQueryClient();
  const preflight = useQuery({
    ...jobsPreflight(pid),
    enabled,
    refetchInterval: (q) => (q.state.data?.active_job ? 5000 : false),
  });
  const activeId = preflight.data ? (preflight.data.active_job?.id ?? null) : undefined;
  // undefined = not observed yet: the first reading is a baseline, not a
  // transition.
  const seen = useRef<string | null | undefined>(undefined);

  useEffect(() => {
    if (activeId === undefined) return;
    if (seen.current !== undefined && seen.current !== activeId) {
      void invalidateProject(qc, pid);
    }
    seen.current = activeId;
  }, [activeId, qc, pid]);
}
