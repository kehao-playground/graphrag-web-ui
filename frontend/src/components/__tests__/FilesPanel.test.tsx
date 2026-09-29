import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { jobsPreflight } from "../../api/queries";
import { message } from "antd";
import { MemoryRouter } from "react-router-dom";
import FilesPanel from "../FilesPanel";
import { stubFetch } from "../../testing/stubFetch";

// Same mock discipline as Projects.test.tsx: branch by URL so a lookup-key
// mistake (wrong endpoint) cannot silently pass on another call's payload.
// The real api client stays under test; only fetch is stubbed.
const api = vi.fn(async (path: string, init?: RequestInit) => {
  // Route by URL like Projects.test.tsx: the FilesOut fixture is only
  // served on the /files endpoint, so a wrong-path query starves the panel.
  if (path === "/api/projects/p1/files" && init?.method !== "POST") {
    return new Response(JSON.stringify(filesBody), { status: 200 });
  }
  if (path === "/api/projects/p1/tags") {
    return new Response(JSON.stringify({ tags: [{ name: "policy", count: 1 }] }), { status: 200 });
  }
  if (path === "/api/projects/p1/jobs/preflight") {
    if (preflightBody === null) {
      return new Response(JSON.stringify({ detail: "preflight exploded" }), { status: 500 });
    }
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
  // Tag writes answer 204; a name in tagFailures answers 404 instead.
  const tagPath = /^\/api\/projects\/p1\/files\/([^/]+)\/tags$/.exec(path);
  if (tagPath) {
    if (tagFailures.includes(decodeURIComponent(tagPath[1]))) {
      return new Response(JSON.stringify({ detail: "file not found", code: "file_not_found" }), { status: 404 });
    }
    return new Response(null, { status: 204 });
  }
  // Uploads: the multipart file's name picks the canned outcome.
  if (path === "/api/projects/p1/files" && init?.method === "POST") {
    const file = (init.body as FormData).get("file") as File;
    const failure = uploadFailures[file.name];
    if (failure) return new Response(JSON.stringify(failure.body), { status: failure.status });
    return new Response(JSON.stringify({ name: file.name, size: file.size }), { status: 201 });
  }
  if (path === "/api/projects/p1/files/notes.txt" && init?.method === "DELETE") {
    return new Response(null, { status: 204 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
stubFetch(api);

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
  usage_bytes: 1536, quota_bytes: 10240, max_file_bytes: 50 * 1024 * 1024,
  ingest_check: "available", has_baseline: true,
};

// Mutable so the banner test can swap ingest_check; reset per test so the
// suite stays order-independent.
let filesBody: Record<string, unknown> = FILES_BODY;

// PreflightOut as far as the panel cares: the active job that freezes
// document work (spec 5.2b), or null. A null body makes the endpoint 500.
let preflightBody: Record<string, unknown> | null = { active_job: null };

// BulkDeleteOut: what the server actually removed, and what it could not.
let bulkDeleteBody: Record<string, unknown> = { deleted: 0, bytes: 0, failed: [] };

// File names whose tag writes 404, and upload names with a canned error.
let tagFailures: string[] = [];
let uploadFailures: Record<string, { status: number; body: Record<string, unknown> }> = {};

beforeEach(() => {
  filesBody = FILES_BODY;
  preflightBody = { active_job: null };
  bulkDeleteBody = { deleted: 0, bytes: 0, failed: [] };
  tagFailures = [];
  uploadFailures = {};
  api.mockClear();
});

// The tag writes the panel sent, as (method, file, tags) triples.
const tagCalls = () => api.mock.calls
  .filter(([path]) => /\/files\/[^/]+\/tags$/.test(path))
  .map(([path, init]) => ({
    method: init?.method,
    name: decodeURIComponent(/\/files\/([^/]+)\/tags$/.exec(path)![1]),
    tags: JSON.parse(init!.body as string).tags as string[],
  }));
const listReads = () => api.mock.calls
  .filter(([path, init]) => path === "/api/projects/p1/files" && init?.method !== "POST").length;
const uploadPosts = () => api.mock.calls
  .filter(([path, init]) => path === "/api/projects/p1/files" && init?.method === "POST").length;
// antd nests role=dialog elements; the modal is addressed by its title.
const findModal = async (title: string) =>
  (await screen.findByText(title)).closest<HTMLElement>(".ant-modal")!;
const uploadInput = () => document.querySelector<HTMLInputElement>("input[type=file]")!;
const txt = (name: string, bytes = 4) => new File(["x".repeat(bytes)], name, { type: "text/plain" });

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
    <QueryClientProvider client={createQueryClient()}>
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

test("no ingest-check banner before the project has a baseline", async () => {
  // A brand-new project: nothing to act on, so no jargon banner (R4-31).
  renderPanel({ body: { ...FILES_BODY, ingest_check: "unavailable_no_baseline", has_baseline: false } });
  await screen.findByText("notes.txt");
  expect(screen.queryByText(/靜默略過偵測已關閉/)).not.toBeInTheDocument();
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
  expect((await screen.findAllByText(/2 份文件/)).length).toBeGreaterThan(0);
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
  await screen.findAllByText(/2 份文件/);
  await userEvent.click(document.querySelector<HTMLElement>(".ant-modal-confirm-btns .ant-btn-dangerous")!);
  expect(await screen.findByText("已刪除 1 份文件")).toBeInTheDocument();
  expect(await screen.findByText(/1 份文件無法刪除：draft.md/)).toBeInTheDocument();
});

test("a removed row offers no selection and no actions", async () => {
  renderPanel();
  const removedRow = (await screen.findByText("gone.md")).closest("tr")!;
  expect(within(removedRow).queryByRole("checkbox")).not.toBeInTheDocument();
  expect(within(removedRow).queryByRole("button", { name: /刪\s*除/ })).not.toBeInTheDocument();
});

test("a failed preflight stays quiet: no toast, the listing still renders", async () => {
  preflightBody = null;
  const toast = vi.spyOn(message, "error");
  const qc = createQueryClient();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FilesPanel projectId="p1" inputFileType="text" canEdit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("notes.txt")).toBeInTheDocument();
  // Settled = the query has recorded the 500, so any toast has fired.
  await waitFor(() => expect(qc.getQueryState(jobsPreflight("p1").queryKey)?.status)
    .toBe("error"));
  expect(toast).not.toHaveBeenCalled();
  toast.mockRestore();
});

test("uploader and delete are disabled with a reason while indexing", async () => {
  renderPanel({ activeJob: { id: "j1", type: "index" } });
  expect(await screen.findByText(/索引任務進行中，暫停文件異動/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "刪除所選" })).toBeDisabled();
});

test("clicking a row name opens the preview drawer with the head window", async () => {
  renderPanel();
  await userEvent.click(await screen.findByText("notes.txt"));
  expect(await screen.findByText("PREVIEW-BODY")).toBeInTheDocument();
});

test("tag selected adds the chosen tags to every selected file", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /draft.md/ }));
  await userEvent.click(screen.getByRole("button", { name: "標記所選…" }));
  const dialog = await findModal("標記 2 份文件");
  await userEvent.type(within(dialog).getByRole("combobox"), "hr{enter}");
  const reads = listReads();
  await userEvent.click(within(dialog).getByRole("button", { name: "加入 2 份文件" }));
  expect(await screen.findByText("已更新 2 份文件的標籤")).toBeInTheDocument();
  expect(tagCalls()).toEqual(expect.arrayContaining([
    { method: "POST", name: "notes.txt", tags: ["hr"] },
    { method: "POST", name: "draft.md", tags: ["hr"] },
  ]));
  expect(tagCalls()).toHaveLength(2);
  // The listing refreshes so the new tags show.
  await waitFor(() => expect(listReads()).toBeGreaterThan(reads));
});

test("tag selected can remove tags, and names the files that failed", async () => {
  tagFailures = ["draft.md"];
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  await userEvent.click(screen.getByRole("checkbox", { name: /draft.md/ }));
  await userEvent.click(screen.getByRole("button", { name: "標記所選…" }));
  const dialog = await findModal("標記 2 份文件");
  await userEvent.type(within(dialog).getByRole("combobox"), "policy{enter}");
  await userEvent.click(within(dialog).getByRole("button", { name: "從 2 份文件移除" }));
  expect(await screen.findByText(/1 份文件的標籤未能更新：draft.md/)).toBeInTheDocument();
  expect(tagCalls().every((c) => c.method === "DELETE")).toBe(true);
});

test("editing a row's tags sends only the difference", async () => {
  renderPanel();
  const row = (await screen.findByText("notes.txt")).closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: "編輯標籤" }));
  const dialog = await findModal("notes.txt 的標籤");
  // notes.txt carries "policy": drop it, add "hr".
  await userEvent.click(within(within(dialog).getByTitle("policy")).getByLabelText("close"));
  await userEvent.type(within(dialog).getByRole("combobox"), "hr{enter}");
  await userEvent.click(within(dialog).getByRole("button", { name: /儲\s*存/ }));
  expect(await screen.findByText("已更新 notes.txt 的標籤")).toBeInTheDocument();
  expect(tagCalls()).toEqual(expect.arrayContaining([
    { method: "POST", name: "notes.txt", tags: ["hr"] },
    { method: "DELETE", name: "notes.txt", tags: ["policy"] },
  ]));
  expect(tagCalls()).toHaveLength(2);
});

test("tagging stays available while indexing (tags are not input)", async () => {
  renderPanel({ activeJob: { id: "j1", type: "index" } });
  const row = (await screen.findByText("notes.txt")).closest("tr")!;
  await screen.findByText(/索引任務進行中/);
  expect(within(row).getByRole("button", { name: "編輯標籤" })).toBeEnabled();
});

test("a bulk upload ends in one summary toast and one listing refresh", async () => {
  uploadFailures = { "bad.txt": { status: 400, body: { detail: "x", code: "file_empty" } } };
  renderPanel();
  await screen.findByText("notes.txt");
  const reads = listReads();
  await userEvent.upload(uploadInput(), [txt("a.txt"), txt("b.txt"), txt("bad.txt")]);
  expect(await screen.findByText(/已上傳 2 \/ 3 份文件，失敗：bad.txt/)).toBeInTheDocument();
  // Only the summary: no per-file success toast.
  expect(screen.queryByText("已上傳 a.txt")).not.toBeInTheDocument();
  await waitFor(() => expect(listReads()).toBe(reads + 1));
});

test("a single upload keeps the named toast", async () => {
  renderPanel();
  await screen.findByText("notes.txt");
  await userEvent.upload(uploadInput(), txt("a.txt"));
  expect(await screen.findByText("已上傳 a.txt")).toBeInTheDocument();
});

test("the uploader names the per-file limit and refuses a larger file before sending", async () => {
  renderPanel({ body: { ...FILES_BODY, max_file_bytes: 1024 } });
  expect(await screen.findByText(/每個檔案上限 1.0 KiB/)).toBeInTheDocument();
  await userEvent.upload(uploadInput(), txt("big.txt", 2048));
  expect(await screen.findByText(/big.txt 超過每個檔案 1.0 KiB 的上限/)).toBeInTheDocument();
  expect(uploadPosts()).toBe(0);
});

test("an oversized file in a drop is named once in the summary", async () => {
  renderPanel({ body: { ...FILES_BODY, max_file_bytes: 1024 } });
  await screen.findByText("notes.txt");
  await userEvent.upload(uploadInput(), [txt("a.txt"), txt("big.txt", 2048)]);
  expect(await screen.findByText("已上傳 1 / 2 份文件，失敗：big.txt（超過每個檔案 1.0 KiB 的上限）")).toBeInTheDocument();
  expect(uploadPosts()).toBe(1);
});

test("deleting a selected row drops it from the selection", async () => {
  renderPanel();
  await userEvent.click(await screen.findByRole("checkbox", { name: /notes.txt/ }));
  expect(screen.getByRole("button", { name: "刪除所選" })).toBeEnabled();
  const row = screen.getByText("notes.txt").closest("tr")!;
  await userEvent.click(within(row).getByRole("button", { name: /刪\s*除/ }));
  await userEvent.click(document.querySelector<HTMLElement>(".ant-popconfirm .ant-btn-dangerous")!);
  await screen.findByText("文件已刪除");
  await waitFor(() => expect(screen.getByRole("button", { name: "刪除所選" })).toBeDisabled());
  expect(screen.getByRole("button", { name: "標記所選…" })).toBeDisabled();
});

// --- empty states (R4-30)

test("an empty project says where to add documents", async () => {
  renderPanel({ body: { ...FILES_BODY, files: [], usage_bytes: 0 } });
  expect(await screen.findByText("尚無文件，將檔案拖曳到上方區域即可上傳。")).toBeInTheDocument();
});

test("a filter matching nothing says so, not 'no documents'", async () => {
  renderPanel({ route: "/?state=skipped" });
  expect(await screen.findByText("沒有符合篩選條件的文件。")).toBeInTheDocument();
  expect(screen.queryByText(/尚無文件/)).not.toBeInTheDocument();
});

test("row delete is a quiet link; red is kept for the confirmation (R4-38)", async () => {
  renderPanel();
  const row = (await screen.findByText("notes.txt")).closest("tr")!;
  const del = within(row).getByRole("button", { name: /刪\s*除/ });
  expect(del).not.toHaveClass("ant-btn-dangerous");
});

test("the tags column keeps its header on one line (R4-32)", async () => {
  renderPanel();
  const header = await screen.findByRole("columnheader", { name: "標籤" });
  expect(header).toHaveStyle({ whiteSpace: "nowrap" });
});
