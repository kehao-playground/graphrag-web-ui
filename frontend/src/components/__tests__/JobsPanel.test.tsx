import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import { Modal } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import JobsPanel from "../JobsPanel";
import type * as ApiClient from "../../api/client";

// Job row fixture matching backend JobOut (types.ts Job).
function job(over: Record<string, unknown> = {}) {
  return {
    id: "j1",
    project_id: "p1",
    type: "index",
    method: "standard",
    status: "queued",
    display_status: "queued",
    cancel_requested_at: null,
    exit_code: null,
    error: null,
    stats: null,
    queued_by: "u1",
    queued_at: "2026-08-21T00:00:00Z",
    started_at: null,
    finished_at: null,
    argv: [],
    ...over,
  };
}

const PREFLIGHT = {
  active_job: null,
  last_run: {
    type: "index",
    status: "succeeded",
    finished_at: "2026-08-20T00:00:00Z",
    total_runtime_seconds: 120.4,
    num_documents: 3,
    update_documents: null,
  },
  cache_bytes: 1024,
  cache_quota_mb: 512,
  disk_free_mb: 50000,
  disk_watermark_mb: 2048,
};

// Same mock discipline as FilesPanel/SettingsPanel tests: branch by URL (and
// method for POST) so a wrong endpoint or body cannot silently pass.
let jobsList: unknown[] = [job()];
let postResponse: () => Response = () => new Response(JSON.stringify(job({ id: "j9" })), { status: 201 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/jobs/preflight") {
    return new Response(JSON.stringify(PREFLIGHT), { status: 200 });
  }
  if (path === "/api/projects/p1/jobs" && init?.method === "POST") {
    return postResponse();
  }
  if (path === "/api/projects/p1/jobs") {
    return new Response(JSON.stringify(jobsList), { status: 200 });
  }
  if (path === "/api/jobs/j1/cancel" && init?.method === "POST") {
    return new Response(JSON.stringify({ detail: "已請求取消" }), { status: 202 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
// Real bodyOf/detailOf stay under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string, RequestInit?]),
}));

// Modal.confirm portals live outside the React tree RTL unmounts; close and
// purge them between tests so leftover ok/cancel buttons (which animate away
// asynchronously) don't collide across button queries.
afterEach(() => {
  Modal.destroyAll();
  cleanup();
  // Keep .ant-message alive: antd's static message holder reuses that node;
  // removing it detaches the holder and later message.error() renders nowhere.
  document.querySelectorAll(".ant-modal-root").forEach((el) => el.remove());
});
// Shared fixtures reset per test: jobsList is mutated by the cancelling test
// and postResponse by the 409 test; later tests must not inherit either.
beforeEach(() => {
  jobsList = [job()];
  postResponse = () => new Response(JSON.stringify(job({ id: "j9" })), { status: 201 });
});


function mount(canEdit: boolean) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <JobsPanel projectId="p1" canEdit={canEdit} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("launch: modal shows 上次執行 summary, confirm POSTs {type:index, method:standard}", async () => {
  mount(true);
  const user = userEvent.setup();
  // Wait for the preflight fetch so the modal deterministically shows last_run.
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs/preflight"));
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  expect(await screen.findByText("上次執行:約 120 秒、3 份文件")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /^開\s?始$/ }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs", {
      method: "POST",
      body: JSON.stringify({ type: "index", method: "standard" }),
    }));
});

test("409 from POST surfaces the backend detail via message.error", async () => {
  postResponse = () => new Response(JSON.stringify({ detail: "此專案已有進行中的索引任務" }), { status: 409 });
  mount(true);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  await user.click(await screen.findByRole("button", { name: /^開\s?始$/ }));
  expect(await screen.findByText("此專案已有進行中的索引任務")).toBeInTheDocument();
});

test("queued row shows 取消 and POSTs cancel after Popconfirm", async () => {
  mount(true);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^取\s?消$/ }));
  await user.click(await screen.findByRole("button", { name: "確定取消" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/jobs/j1/cancel", { method: "POST" }));
});

test("cancelling display_status renders the cancelling tag", async () => {
  jobsList = [job({
    status: "running",
    display_status: "cancelling",
    cancel_requested_at: "2026-08-21T00:01:00Z",
    started_at: "2026-08-21T00:00:30Z",
  })];
  mount(true);
  expect(await screen.findByText("cancelling")).toBeInTheDocument();
  // cancel already requested → no cancel button
  expect(screen.queryByRole("button", { name: /^取\s?消$/ })).not.toBeInTheDocument();
});

test("canEdit=false: launch disabled, no 取消, 日誌 still available", async () => {
  mount(false);
  // Await the table row (the type Select label renders earlier than rows).
  await screen.findByText("queued");
  expect(screen.getByRole("button", { name: "開始索引" })).toBeDisabled();
  expect(screen.queryByRole("button", { name: /^取\s?消$/ })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^日\s?誌$/ })).toBeInTheDocument();
});


test("modal warns when cache exceeds quota and disk is under watermark", async () => {
  postResponse = () => new Response(JSON.stringify(job({ id: "j9" })), { status: 201 });
  PREFLIGHT.cache_bytes = 600 * 1024 * 1024;
  PREFLIGHT.disk_free_mb = 1000;
  mount(true);
  const user = userEvent.setup();
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs/preflight"));
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  expect(await screen.findByText(/快取已超過上限/)).toBeInTheDocument();
  expect(screen.getByText(/磁碟水位不足/)).toBeInTheDocument();
});
