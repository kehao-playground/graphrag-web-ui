import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import FilesPanel from "../FilesPanel";
import type * as ApiClient from "../../api/client";

// Same mock discipline as Projects.test.tsx: branch by URL so a lookup-key
// mistake (wrong endpoint) cannot silently pass on another call's payload.
// Real bodyOf/detailOf stay under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (path: string) => {
    // Route by URL like Projects.test.tsx: the FilesOut fixture is only
    // served on the /files endpoint, so a wrong-path query starves the panel.
    if (path === "/api/projects/p1/files") {
      return new Response(JSON.stringify(filesBody), { status: 200 });
    }
    if (path === "/api/projects/p1/tags") {
      return new Response(JSON.stringify({ tags: [{ name: "policy", count: 1 }] }), { status: 200 });
    }
    if (path === "/api/projects/p1/jobs/preflight") {
      return new Response(JSON.stringify(preflightBody), { status: 200 });
    }
    if (path === "/api/projects/p1/files/notes.txt/preview") {
      return new Response(JSON.stringify({
        text: "PREVIEW-BODY", offset: 0, total_size: 12, match: false,
      }), { status: 200 });
    }
    if (path === "/api/projects/p1/files:bulk-delete") {
      return new Response(JSON.stringify(bulkDeleteBody), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  }),
}));

// FileListOut fixture (Task 5): FileEntryOut rows carry index_state and
// tags, the response carries ingest_check/has_baseline. One row per state
// the split must render, plus a removed row with all-null file facts.
const FILES_BODY = {
  files: [
    { name: "notes.txt", size: 1024, modified_at: "2026-08-19T00:00:00Z",
      sha256: "aa", index_state: "indexed", tags: ["policy"] },
    { name: "draft.md", size: 512, modified_at: "2026-08-19T01:00:00Z",
      sha256: "bb", index_state: "modified", tags: [] },
    { name: "gone.md", size: null, modified_at: null, sha256: null,
      index_state: "removed", tags: [] },
  ],
  usage_bytes: 1536, quota_bytes: 10240,
  ingest_check: "available", has_baseline: true,
};

// Mutable so the banner test can swap ingest_check; reset per test so the
// suite stays order-independent.
let filesBody: Record<string, unknown> = FILES_BODY;

// PreflightOut as far as the panel cares: the active job that freezes
// document work (spec 5.2b), or null.
let preflightBody: Record<string, unknown> = { active_job: null };

// BulkDeleteOut: what the server actually removed, and what it could not.
let bulkDeleteBody: Record<string, unknown> = { deleted: 0, bytes: 0, failed: [] };

beforeEach(() => {
  filesBody = FILES_BODY;
  preflightBody = { active_job: null };
  bulkDeleteBody = { deleted: 0, bytes: 0, failed: [] };
});

// Composition wrapper: the panel under a fresh QueryClient, inside a router
// whose initial URL can seed the ?state= filter (the slice ③ landing path).
// activeJob seeds the preflight mock with a freezing job (index/update).
function renderPanel(opts: {
  route?: string;
  body?: Record<string, unknown>;
  activeJob?: { id: string; type: string } | null;
} = {}) {
  filesBody = opts.body ?? FILES_BODY;
  if (opts.activeJob !== undefined) preflightBody = { active_job: opts.activeJob };
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[opts.route ?? "/"]}>
        <FilesPanel projectId="p1" inputFileType="text" canEdit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders file names and quota percent from GET files", async () => {
  renderPanel();
  expect(await screen.findByText("notes.txt")).toBeInTheDocument();
  expect(screen.getByText("draft.md")).toBeInTheDocument();
  // 1536 / 10240 = 15%
  expect(screen.getByText("15%")).toBeInTheDocument();
  // human-readable binary units: sub-KiB sizes stay in bytes, larger
  // values scale KiB -> MiB -> GiB (5 GB quota renders "4.9 GiB", not
  // "5120000.0 KiB")
  expect(screen.getByText("1.0 KiB")).toBeInTheDocument();
  expect(screen.getByText("512 B")).toBeInTheDocument();
  expect(screen.getByText("已使用 1.5 KiB / 10.0 KiB")).toBeInTheDocument();
});

test("renders an index state per row, and a removed row shows no size", async () => {
  renderPanel();
  expect(await screen.findByText("已索引")).toBeInTheDocument();
  expect(screen.getByText("已修改")).toBeInTheDocument();
  expect(screen.getByText("已刪除")).toBeInTheDocument();
  const removedRow = screen.getByText("gone.md").closest("tr")!;
  expect(within(removedRow).getByText("—")).toBeInTheDocument();
});

test("a removed row explains that only a full rebuild clears it", async () => {
  renderPanel();
  await userEvent.hover(await screen.findByText("已刪除"));
  expect(await screen.findByText(/完整重建/)).toBeInTheDocument();
});

test("filename search filters client-side", async () => {
  renderPanel();
  await userEvent.type(await screen.findByPlaceholderText("搜尋檔名…"), "draft");
  expect(screen.getByText("draft.md")).toBeInTheDocument();
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
});

test("the state filter is seeded from the ?state= query param", async () => {
  renderPanel({ route: "/projects/p1/files?state=modified" });
  expect(await screen.findByText("draft.md")).toBeInTheDocument();
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument();
});

test("an unavailable ingest check shows one banner naming the reason", async () => {
  renderPanel({ body: { ...FILES_BODY, ingest_check: "unavailable_title_column" } });
  expect(await screen.findByText(/靜默略過偵測已關閉/)).toBeInTheDocument();
  // One banner for the table, never one per row.
  expect(screen.getAllByText(/靜默略過偵測已關閉/)).toHaveLength(1);
});

test("no banner when the check is available", async () => {
  renderPanel();
  await screen.findByText("notes.txt");
  expect(screen.queryByText(/靜默略過偵測已關閉/)).not.toBeInTheDocument();
});

test("the not-yet-indexed bar counts new+modified and links to jobs", async () => {
  renderPanel();
  // draft.md is modified, notes.txt indexed, gone.md removed → 1 pending.
  expect(await screen.findByText("尚有 1 份文件未建立索引")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "前往任務" })).toHaveAttribute("href", "/projects/p1/jobs");
});

test("bulk delete confirms with count and total size", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /draft.md/ }));
  await userEvent.click(screen.getByRole("button", { name: "刪除所選" }));
  // Deleting 30 documents is not the same act as deleting one.
  // antd renders the confirm title twice in the DOM (title div + an
  // internal span), so "at least one visible copy" is the honest pin.
  expect((await screen.findAllByText(/2 個檔案/)).length).toBeGreaterThan(0);
  expect(screen.getAllByText(/1.5 KiB/).length).toBeGreaterThan(0);
});

test("a partial bulk delete reports the server's count and names the failures", async () => {
  bulkDeleteBody = { deleted: 1, bytes: 1024, failed: ["draft.md"] };
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /draft.md/ }));
  await userEvent.click(screen.getByRole("button", { name: "刪除所選" }));
  // The confirm's danger button (antd spaces two-character CJK labels, and
  // the rows carry delete buttons of their own, so the name cannot pin it).
  await screen.findAllByText(/2 個檔案/);
  await userEvent.click(document.querySelector<HTMLElement>(".ant-modal-confirm-btns .ant-btn-dangerous")!);
  expect(await screen.findByText("已刪除 1 個檔案")).toBeInTheDocument();
  expect(await screen.findByText(/1 個檔案無法刪除：draft.md/)).toBeInTheDocument();
});

test("a removed row offers no selection and no actions", async () => {
  renderPanel();
  const removedRow = (await screen.findByText("gone.md")).closest("tr")!;
  expect(within(removedRow).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(within(removedRow).queryByRole("button", { name: "刪除" })).not.toBeInTheDocument();
});

test("uploader and delete are disabled with a reason while indexing", async () => {
  renderPanel({ activeJob: { id: "j1", type: "index" } });
  expect(await screen.findByText(/索引作業執行中，暫停文件異動/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "刪除所選" })).toBeDisabled();
});

test("clicking a row name opens the preview drawer with the head window", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("notes.txt"));
  expect(await screen.findByText("PREVIEW-BODY")).toBeInTheDocument();
});
