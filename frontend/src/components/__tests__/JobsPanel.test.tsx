import { render, screen, waitFor, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import { Modal } from "antd";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter } from "react-router-dom";
import JobsPanel from "../JobsPanel";
import { stubFetch } from "../../testing/stubFetch";

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
  graphrag: "3.1.2",
};

// The launch confirm names the document count from /health (R4-25).
const HEALTH = {
  active_job: null, api_key_missing: false, artifacts_stale: false,
  files: { indexed: 4, modified: 1, new: 2, removed: 1, skipped: 0, total: 8 },
  has_baseline: true, ingest_check: "available", last_index: null, latest_run: null,
};

// The jobs page lists only the launchable types (decision D2), one page
// of 20 at a time (R3-10).
const LIST_PREFIX = "/api/projects/p1/jobs?type=index&type=update&";
const LIST = `${LIST_PREFIX}limit=20&offset=0`;

// Same mock discipline as FilesPanel/SettingsPanel tests: branch by URL (and
// method for POST) so a wrong endpoint or body cannot silently pass.
let jobsList: unknown[] = [job()];
// The envelope's total; null means "the whole history is jobsList".
let jobsTotal: number | null = null;
let postResponse: () => Response = () => new Response(JSON.stringify(job({ id: "j9" })), { status: 201 });
let preflightResponse: () => Response = () => new Response(JSON.stringify(PREFLIGHT), { status: 200 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/jobs/preflight") {
    return preflightResponse();
  }
  if (path === "/api/projects/p1/jobs" && init?.method === "POST") {
    return postResponse();
  }
  if (path.startsWith(LIST_PREFIX)) {
    const total = jobsTotal ?? jobsList.length;
    return new Response(JSON.stringify({ items: jobsList, total }), { status: 200 });
  }
  if (path === "/api/projects/p1/health") {
    return new Response(JSON.stringify(HEALTH), { status: 200 });
  }
  if (path === "/api/jobs/j1/cancel" && init?.method === "POST") {
    return new Response(JSON.stringify({ detail: "cancellation requested" }), { status: 202 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
// The real api client stays under test; only fetch is stubbed.
stubFetch(apiMock);

// The log drawer streams over EventSource, which jsdom lacks.
class NoopEventSource {
  addEventListener() {}
  close() {}
}
vi.stubGlobal("EventSource", NoopEventSource);

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
  jobsTotal = null;
  postResponse = () => new Response(JSON.stringify(job({ id: "j9" })), { status: 201 });
  preflightResponse = () => new Response(JSON.stringify(PREFLIGHT), { status: 200 });
});


function mount(canEdit: boolean, route = "/") {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[route]}>
        <JobsPanel projectId="p1" canEdit={canEdit} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("launch: modal shows 上次執行 summary, confirm POSTs {type:index, method:standard}", async () => {
  mount(true);
  const user = userEvent.setup();
  // Wait for the preflight fetch so the modal deterministically shows last_run.
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs/preflight", expect.anything()));
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  expect(await screen.findByText("上次執行：約 120 秒、3 份文件")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: /^開\s?始$/ }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ type: "index", method: "standard" }),
    })));
});

test("409 from POST surfaces the backend detail via message.error", async () => {
  postResponse = () => new Response(JSON.stringify({ detail: "此專案已有進行中的索引任務" }), { status: 409 });
  mount(true);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  await user.click(await screen.findByRole("button", { name: /^開\s?始$/ }));
  expect(await screen.findByText("此專案已有進行中的索引任務")).toBeInTheDocument();
});

// The shared preflight query is quiet by default (the documents and
// workbench panes treat it as decoration); this pane opts back into the
// toast because its launch guardrail depends on it.
test("a failed preflight toasts here, once", async () => {
  preflightResponse = () =>
    new Response(JSON.stringify({ detail: "preflight exploded" }), { status: 500 });
  mount(true);
  expect(await screen.findByText("preflight exploded")).toBeInTheDocument();
});

test("queued row shows 取消 and POSTs cancel after Popconfirm", async () => {
  mount(true);
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^取\s?消$/ }));
  await user.click(await screen.findByRole("button", { name: "確定取消" }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/jobs/j1/cancel", expect.objectContaining({ method: "POST" })));
});

test("cancelling display_status renders the cancelling tag", async () => {
  jobsList = [job({
    status: "running",
    display_status: "cancelling",
    cancel_requested_at: "2026-08-21T00:01:00Z",
    started_at: "2026-08-21T00:00:30Z",
  })];
  mount(true);
  expect(await screen.findByText("取消中")).toBeInTheDocument();
  // cancel already requested → no cancel button
  expect(screen.queryByRole("button", { name: /^取\s?消$/ })).not.toBeInTheDocument();
});

test("canEdit=false: launch disabled, no 取消, 日誌 still available", async () => {
  mount(false);
  // Await the table row (the type Select label renders earlier than rows).
  await screen.findByText("排隊中");
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
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/jobs/preflight", expect.anything()));
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  expect(await screen.findByText(/快取已超過上限/)).toBeInTheDocument();
  expect(screen.getByText(/磁碟水位不足/)).toBeInTheDocument();
});

test("the list asks for index and update jobs only", async () => {
  mount(true);
  await screen.findByText("排隊中");
  expect(apiMock).toHaveBeenCalledWith(LIST, expect.anything());
});

test("the history pages through the server instead of stopping at one page (R3-10)", async () => {
  jobsTotal = 25;
  mount(true);
  await screen.findByText("排隊中");
  await userEvent.click(screen.getByTitle("2"));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
    `${LIST_PREFIX}limit=20&offset=20`, expect.anything(),
  ));
});

test("statuses render translated, unknown ones raw", async () => {
  jobsList = [
    job({ id: "a", status: "failed", display_status: "failed(interrupted)" }),
    job({ id: "b", status: "succeeded", display_status: "succeeded" }),
    job({ id: "c", status: "failed", display_status: "exploded" }),
  ];
  mount(true);
  expect(await screen.findByText("失敗（中斷）")).toBeInTheDocument();
  expect(screen.getByText("成功")).toBeInTheDocument();
  expect(screen.getByText("exploded")).toBeInTheDocument();
});

// R3-20 / R4-25: say a job holds the project before the click, not after.
test("an active job shows a notice and disables Start", async () => {
  preflightResponse = () => new Response(JSON.stringify({
    ...PREFLIGHT, active_job: job({ id: "t1", type: "test_run", status: "running" }),
  }), { status: 200 });
  mount(true);
  expect(await screen.findByText(/測試執行正在進行/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "開始索引" })).toBeDisabled();
});

// F3-01: the spec §10 pre-check, read from the preflight.
test("a missing graphrag CLI shows an error and disables Start", async () => {
  preflightResponse = () =>
    new Response(JSON.stringify({ ...PREFLIGHT, graphrag: "not-installed" }), { status: 200 });
  mount(true);
  expect(await screen.findByText(/找不到 graphrag/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "開始索引" })).toBeDisabled();
});

test("the confirm names the document count, the method and the billing", async () => {
  mount(true);
  const user = userEvent.setup();
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/health", expect.anything()));
  await user.click(await screen.findByRole("button", { name: "開始索引" }));
  // index = every document still in input/ (total 8 minus 1 removed)
  expect(await screen.findByText("將以「標準」方法索引 7 份文件。")).toBeInTheDocument();
  expect(screen.getByText(/計費/)).toBeInTheDocument();
});

// R4-26: a running row shows time moving.
test("a running row shows its elapsed time", async () => {
  jobsList = [job({
    status: "running", display_status: "running",
    started_at: new Date(Date.now() - 125_000).toISOString(),
  })];
  mount(true);
  expect(await screen.findByText(/^2 分 \d+ 秒$/)).toBeInTheDocument();
});

// The overview's "open the log" links land here with ?log=<id>.
test("?log= opens that job's log drawer, titled with the job", async () => {
  jobsList = [job({ status: "running", display_status: "running", started_at: "2026-08-21T00:00:30Z" })];
  mount(true, "/?log=j1");
  expect(await screen.findByText(/^索引 · 標準 · 開始於 /)).toBeInTheDocument();
});
