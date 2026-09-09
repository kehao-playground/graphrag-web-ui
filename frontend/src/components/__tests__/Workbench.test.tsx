import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import { Modal } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workbench from "../tests/Workbench";
import type * as ApiClient from "../../api/client";
import { MATRIX, SETS, QUESTIONS, PREFLIGHT } from "./workbenchFixtures";

// Same mock discipline as FilesPanel/JobsPanel tests: branch by URL (and
// method for POST) so a wrong endpoint or body cannot silently pass.
let preflightBody: Record<string, unknown> = PREFLIGHT;
let postResponse: () => Response = () =>
  new Response(JSON.stringify({ ...MATRIX.runs[0], id: "run-9" }), { status: 201 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/question-sets") return json(SETS);
  if (path === "/api/projects/p1/question-sets/s1/questions") return json(QUESTIONS);
  if (path === "/api/projects/p1/test-runs" && init?.method === "POST") return postResponse();
  if (path === "/api/projects/p1/test-runs") return json(MATRIX);
  if (path === "/api/projects/p1/jobs/preflight") return json(preflightBody);
  return json({});
});
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string, RequestInit?]),
}));

// Modal.confirm portals live outside the React tree RTL unmounts; purge
// them between tests (JobsPanel.test pattern).
afterEach(() => {
  Modal.destroyAll();
  cleanup();
  // Keep .ant-message alive: antd's static message holder reuses that node.
  document.querySelectorAll(".ant-modal-root").forEach((el) => el.remove());
});
beforeEach(() => {
  preflightBody = PREFLIGHT;
});

// Composition wrapper: the workbench under a fresh QueryClient. activeJob
// seeds the preflight mock with the blocking job (any type).
function renderWorkbench(opts: { activeJob?: { id: string; type: string } | null } = {}) {
  preflightBody = { ...PREFLIGHT, active_job: opts.activeJob ?? null };
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <Workbench projectId="p1" canUse canRunJobs />
    </QueryClientProvider>,
  );
}

test("the launch dialog states question count and method before committing", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByRole("button", { name: "重跑整組" }));
  // The count comes from GET question-sets/{sid}/questions (20 in the
  // fixture); the method is the raw identifier the run will record. The
  // regex must not match the set picker's selected name (客服常問 20 題).
  expect(await screen.findByText(/將執行 20 題/)).toBeInTheDocument();
  expect(screen.getByText(/local/)).toBeInTheDocument();
  // Committing POSTs the selected set + method to /test-runs.
  await userEvent.click(screen.getByRole("button", { name: "開始執行" }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/test-runs", {
      method: "POST",
      body: JSON.stringify({ set_id: "s1", method: "local" }),
    }));
});

test("a job conflict names which job is running", async () => {
  renderWorkbench({ activeJob: { id: "j1", type: "index" } });
  // Not a dead button, and not a message implying only indexing can block.
  expect(await screen.findByText(/索引作業執行中/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重跑整組" })).toBeDisabled();
});

test("without an active job 重跑整組 stays enabled", async () => {
  renderWorkbench();
  // Wait for the picker to default to s1 (its selected label renders in
  // the selector content), which is what enables the launch button.
  await screen.findByText("客服常問 20 題");
  expect(screen.getByRole("button", { name: "重跑整組" })).toBeEnabled();
});
