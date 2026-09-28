import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { message } from "antd";
import { createQueryClient } from "../queryClient";
import { projectFiles, projectHealth } from "../queries";

// The app's one QueryClient config (R1-49, R1-87): no retries, one error
// toast per failed query/mutation with a meta.silent opt-out, and the
// expensive listing keys stay fresh long enough to survive remounts.

let toast: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  toast = vi.spyOn(message, "error").mockImplementation(() => undefined as never);
});
afterEach(() => vi.restoreAllMocks());

test("a failed query toasts its message once and does not retry", async () => {
  const qc = createQueryClient();
  const fn = vi.fn(async () => { throw new Error("boom"); });
  await qc.fetchQuery({ queryKey: ["x"], queryFn: fn }).catch(() => undefined);
  expect(fn).toHaveBeenCalledTimes(1);
  expect(toast).toHaveBeenCalledTimes(1);
  expect(toast).toHaveBeenCalledWith("boom");
});

test("meta.silent keeps a failed query quiet", async () => {
  const qc = createQueryClient();
  await qc.fetchQuery({
    queryKey: ["y"],
    queryFn: async () => { throw new Error("quiet"); },
    meta: { silent: true },
  }).catch(() => undefined);
  expect(toast).not.toHaveBeenCalled();
});

test("a failed mutation toasts unless it opts out", async () => {
  const qc = createQueryClient();
  const loud = qc.getMutationCache().build(qc, {
    mutationFn: async () => { throw new Error("nope"); },
  });
  await loud.execute(undefined).catch(() => undefined);
  expect(toast).toHaveBeenCalledWith("nope");

  toast.mockClear();
  const quiet = qc.getMutationCache().build(qc, {
    mutationFn: async () => { throw new Error("handled"); },
    meta: { silent: true },
  });
  await quiet.execute(undefined).catch(() => undefined);
  expect(toast).not.toHaveBeenCalled();
});

test("health and files are kept fresh and skip focus refetches", () => {
  for (const opts of [projectHealth("p1"), projectFiles("p1")]) {
    expect(opts.staleTime).toBeGreaterThanOrEqual(30_000);
    expect(opts.refetchOnWindowFocus).toBe(false);
  }
  expect(projectHealth("p1").meta?.silent).toBe(true);
  expect(projectFiles("p1").meta?.silent).toBeUndefined();
});
