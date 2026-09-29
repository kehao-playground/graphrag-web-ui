import { renderHook, waitFor } from "@testing-library/react";
import { vi, beforeEach } from "vitest";
import type { ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { jobsPreflight, projectHealth } from "../../api/queries";
import { useActiveJobWatch } from "../project/useActiveJobWatch";
import { stubFetch } from "../../testing/stubFetch";

const PREFLIGHT = {
  last_run: null, cache_bytes: 0, cache_quota_mb: 512,
  disk_free_mb: 50000, disk_watermark_mb: 2048, graphrag: "3.1.2",
};
let active: { id: string } | null = null;
const api = vi.fn(async (url: string) => {
  if (url === "/api/projects/p1/jobs/preflight") {
    return new Response(JSON.stringify({ ...PREFLIGHT, active_job: active }), { status: 200 });
  }
  return new Response("{}", { status: 200 });
});
stubFetch(api);

beforeEach(() => {
  active = null;
});

function setup() {
  const qc = createQueryClient();
  const spy = vi.spyOn(qc, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  renderHook(() => useActiveJobWatch("p1"), { wrapper });
  const invalidated = (key: readonly unknown[]) =>
    spy.mock.calls.some(([f]) => JSON.stringify(f?.queryKey) === JSON.stringify(key));
  return { qc, spy, invalidated };
}

// R1-18 / R1-84 / R4-06: a job ending changes files, health, the matrix and
// the preflight every pane gates on — one transition refreshes them all.
test("an active job finishing invalidates the project's derived reads", async () => {
  active = { id: "j1" };
  const { qc, spy, invalidated } = setup();
  await waitFor(() => expect(qc.getQueryData(jobsPreflight("p1").queryKey)).toBeTruthy());
  expect(spy).not.toHaveBeenCalled(); // the first observation is not a transition

  active = null;
  await qc.refetchQueries({ queryKey: jobsPreflight("p1").queryKey });
  await waitFor(() => expect(invalidated(projectHealth("p1").queryKey)).toBe(true));
  for (const key of [
    ["projects", "p1", "files"], ["projects", "p1", "tags"],
    ["projects", "p1", "jobs", "list"], ["projects", "p1", "test-runs"],
  ]) {
    expect(invalidated(key)).toBe(true);
  }
});

test("a job starting refreshes the health the sidebar badge reads", async () => {
  const { qc, invalidated } = setup();
  await waitFor(() => expect(qc.getQueryData(jobsPreflight("p1").queryKey)).toBeTruthy());
  active = { id: "j2" };
  await qc.refetchQueries({ queryKey: jobsPreflight("p1").queryKey });
  await waitFor(() => expect(invalidated(projectHealth("p1").queryKey)).toBe(true));
});

test("the preflight polls while a job is active and stops after", async () => {
  active = { id: "j1" };
  const { qc } = setup();
  await waitFor(() => expect(qc.getQueryData(jobsPreflight("p1").queryKey)).toBeTruthy());
  const observer = qc.getQueryCache().find({ queryKey: jobsPreflight("p1").queryKey })!.observers[0]!;
  expect(observer.options.refetchInterval).toBeTypeOf("function");
  const interval = observer.options.refetchInterval as (q: unknown) => number | false;
  const query = qc.getQueryCache().find({ queryKey: jobsPreflight("p1").queryKey })!;
  expect(interval(query)).toBe(5000);
  active = null;
  await qc.refetchQueries({ queryKey: jobsPreflight("p1").queryKey });
  expect(interval(query)).toBe(false);
});
