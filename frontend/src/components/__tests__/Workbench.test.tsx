import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import { Modal } from "antd";
import { MemoryRouter } from "react-router-dom";
 import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
 import Workbench from "../tests/Workbench";
import { MATRIX, SETS, QUESTIONS, PREFLIGHT, RESULTS_RUN3, RESULTS_RUN4, cellLabel } from "./workbenchFixtures";
import { stubFetch } from "../../testing/stubFetch";

// Same mock discipline as FilesPanel/JobsPanel tests: branch by URL (and
// method for POST/PUT/PATCH) so a wrong endpoint or body cannot silently
// pass on another call's payload.
let preflightBody: Record<string, unknown> = PREFLIGHT;
let setsBody: { sets: { id: string; name: string; created_at: string }[] } = SETS;
let matrixBody: Record<string, unknown> = MATRIX;
// Every rating PUT the drawer fired, in order.
let rated: { resultId: string; score: string; note?: string }[] = [];
let postResponse: () => Response = () =>
  new Response(JSON.stringify({ ...MATRIX.runs[0], id: "run-9" }), { status: 201 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/question-sets" && init?.method === "POST") {
    return new Response(JSON.stringify({
      id: "s-new", name: JSON.parse(String(init.body)).name, created_at: "2026-09-28T00:00:00Z",
    }), { status: 201 });
  }
  if (path === "/api/projects/p1/question-sets") return json(setsBody);
  if (path.startsWith("/api/projects/p1/question-sets/") && init?.method === "DELETE") {
    return new Response(null, { status: 204 });
  }
  if (path === "/api/projects/p1/question-sets/s1" && init?.method === "PATCH") {
    return json({ ...SETS.sets[0], name: JSON.parse(String(init.body)).name });
  }
  if (path === "/api/projects/p1/question-sets/s1/questions" && init?.method === "POST") {
    return new Response(JSON.stringify(QUESTIONS.questions[0]), { status: 201 });
  }
  if (path === "/api/projects/p1/question-sets/s1/questions") return json(QUESTIONS);
  if (path === "/api/projects/p1/question-sets/s-new/questions") return json({ questions: [] });
  if (path === "/api/test-runs/run-3/results") return json(RESULTS_RUN3);
  if (path === "/api/test-runs/run-4/results") return json(RESULTS_RUN4);
  if (path.startsWith("/api/test-results/") && init?.method === "PUT") {
    const body = JSON.parse(String(init.body)) as { score: string; note: string };
    rated.push({ resultId: path.split("/")[3], score: body.score, note: body.note });
    return json({ score: body.score, note: body.note, rated_by: "u1", rated_at: "2026-09-09T00:00:00Z" });
  }
  if (path === "/api/projects/p1/test-runs" && init?.method === "POST") return postResponse();
  if (path === "/api/projects/p1/test-runs") return json(matrixBody);
  if (path === "/api/projects/p1/jobs/preflight") return json(preflightBody);
  return json({});
});
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}
stubFetch(apiMock);


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
  setsBody = SETS;
  matrixBody = MATRIX;
  rated = [];
  apiMock.mockClear();
});

// The calls a mutation made, as [path, method, parsed body].
function sent(method: string) {
  return apiMock.mock.calls
    .filter(([, init]) => init?.method === method)
    .map(([path, init]) => [path, init?.body ? JSON.parse(String(init.body)) : null]);
}

// Composition wrapper: the workbench under a fresh QueryClient. activeJob
// seeds the preflight mock with the blocking job (any type); route mounts
// it at its real URL so URL-initialized state (?regressions=1) is testable.
function renderWorkbench(opts: {
  activeJob?: { id: string; type: string } | null;
  route?: string;
  canEdit?: boolean;
} = {}) {
  preflightBody = { ...PREFLIGHT, active_job: opts.activeJob ?? null };
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter initialEntries={[opts.route ?? "/projects/p1/tests"]}>
        <Workbench projectId="p1" canUse canRunJobs canEdit={opts.canEdit ?? true} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("the launch dialog states question count and method before committing", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByRole("button", { name: "重跑整組" }));
  // The count comes from GET question-sets/{sid}/questions (20 in the
  // fixture); the method is the raw identifier the run will record.
  // zh-TW: 客服常問 20 題 is the fixture set's selected name the regex must
  // not match.
  expect(await screen.findByText(/將執行 20 題/)).toBeInTheDocument();
  expect(screen.getByText(/local/)).toBeInTheDocument();
  // Committing POSTs the selected set + method to /test-runs.
  await userEvent.click(screen.getByRole("button", { name: "開始執行" }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/test-runs", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ set_id: "s1", method: "local" }),
    })));
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

test("?regressions=1 starts the matrix filtered to regressions", async () => {
  // The slice ③ overview's regressions card deep-links here; the filter
  // must already be applied on arrival, with the checkbox reflecting it.
  renderWorkbench({ route: "/projects/p1/tests?regressions=1" });
  expect(await screen.findByLabelText("只看退步的")).toBeChecked();
  // Q3 regressed (good → poor), Q1 stayed good — same fixture contract
  // as the click-toggle test in RatingMatrix.test.
  expect(screen.getByText("Q3 退貨流程幾天")).toBeInTheDocument();
  expect(screen.queryByText("Q1 保固期多長")).not.toBeInTheDocument();
});


test("keys 1/2/3 rate the open result and advance to the next", async () => {
  renderWorkbench();
  // Open the drawer on run-4's Q1 result (r1). antd focuses the drawer on
  // open, so the keystroke lands inside it — the handler is bound to the
  // drawer, never to document.
  await userEvent.click(await screen.findByLabelText(cellLabel("Q1 保固期多長", 4)));
  expect(await screen.findByText("第 1 / 4 題")).toBeInTheDocument();
  await userEvent.keyboard("1");
  expect(rated).toEqual([{ resultId: "r1", score: "good", note: "" }]);
  // The drawer advanced to the next result of the run (r2, Q3).
  expect(await screen.findByText("第 2 / 4 題")).toBeInTheDocument();
});

test("typing in the note field never rates", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByLabelText(cellLabel("Q1 保固期多長", 4)));
  const note = await screen.findByLabelText("評註");
  await userEvent.click(note);
  await userEvent.keyboard("123");
  expect(rated).toEqual([]);
  expect(note).toHaveValue("123");
});

test("the note rides along with a keyboard rating", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByLabelText(cellLabel("Q1 保固期多長", 4)));
  const note = await screen.findByLabelText("評註");
  await userEvent.click(note);
  await userEvent.keyboard("answer was too vague");
  // Click the drawer body (focusable, tabIndex -1) so the keystroke rates.
  await userEvent.click(screen.getByText("第 1 / 4 題"));
  await userEvent.keyboard("2");
  expect(rated).toEqual([
    { resultId: "r1", score: "fair", note: "answer was too vague" },
  ]);
});

test("rating the last result of a run closes the drawer", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByLabelText(cellLabel("Q7 發票怎麼開", 4)));
  // The errored result opens too — the error is part of what was asked.
  expect(await screen.findByText("boom")).toBeInTheDocument();
  expect(await screen.findByText("第 4 / 4 題")).toBeInTheDocument();
  await userEvent.keyboard("2");
  expect(rated).toEqual([{ resultId: "r4", score: "fair", note: "" }]);
  await waitFor(() => expect(screen.queryByText("第 4 / 4 題")).not.toBeInTheDocument());
});

test("editing a question that has runs warns before forking", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByText("題目 (20)"));
  // q-1's lineage L1 was asked by runs → editing forks it (spec §5.3).
  await userEvent.click(await screen.findByRole("button", { name: "編輯題目:題目 2" }));
  expect(await screen.findByText(/會建立新版本，過去的執行仍保留原本的題目文字/))
    .toBeInTheDocument();
});

test("editing a never-run question opens the editor without the fork warning", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByText("題目 (20)"));
  // q-0's lineage L0 was never asked → in-place edit, no fork to warn about.
  await userEvent.click(screen.getByRole("button", { name: "編輯題目:題目 1" }));
  expect(screen.queryByText(/會建立新版本/)).not.toBeInTheDocument();
  const editor = await screen.findByRole("textbox", { name: "編輯題目" });
  expect(editor).toHaveValue("題目 1");
  await userEvent.clear(editor);
  await userEvent.type(editor, "題目 1 (改)");
  // zh-TW: 儲存 is auto-spaced to 儲 存 by antd's CJK button handling.
  await userEvent.click(screen.getByRole("button", { name: /儲\s*存/ }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/question-sets/s1/questions/q-0", expect.objectContaining({
      method: "PATCH",
      body: JSON.stringify({ text: "題目 1 (改)" }),
    })));
});

test("two selected cells open the side-by-side diff", async () => {
  renderWorkbench();
  // First pick opens the drawer; the cell stays selected after it closes.
  await userEvent.click(await screen.findByLabelText(cellLabel("Q3 退貨流程幾天", 3)));
  expect(await screen.findByText("第 2 / 3 題")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(await screen.findByText(/已選取一格/)).toBeInTheDocument();
  // Second pick on the same question in another run → sentence-level diff.
  await userEvent.click(screen.getByLabelText(cellLabel("Q3 退貨流程幾天", 4)));
  expect(await screen.findByRole("heading", { name: /並排比較/ })).toBeInTheDocument();
  // zh-TW: 需附發票。 exists only in run-3's answer; run-4 dropped it.
  expect(await screen.findByText("需附發票。")).toBeInTheDocument();
  expect(screen.getByText("#13 · 區域 · 09-03")).toBeInTheDocument();
  expect(screen.getByText("#14 · 區域 · 09-04")).toBeInTheDocument();
});

// --- question-set lifecycle and the first-visit landing (R1-02, R3-23, R4-03)

test("a project without question sets lands on the ad-hoc query", async () => {
  setsBody = { sets: [] };
  matrixBody = { runs: [], rows: [] };
  renderWorkbench();
  // Asking is what a first visit is for; the matrix has nothing to show.
  expect(await screen.findByPlaceholderText("輸入問題")).toBeInTheDocument();
});

test("the empty matrix offers to create a set, which becomes the selection", async () => {
  setsBody = { sets: [] };
  matrixBody = { runs: [], rows: [] };
  renderWorkbench();
  await screen.findByPlaceholderText("輸入問題");
  await userEvent.click(screen.getByText("評分矩陣"));
  expect(await screen.findByText(/題組是測試執行要問的題目清單/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "建立題組" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("題組名稱"), "客服 FAQ");
  setsBody = { sets: [{ id: "s-new", name: "客服 FAQ", created_at: "2026-09-28T00:00:00Z" }] };
  await userEvent.click(within(dialog).getByRole("button", { name: /^建\s?立$/ }));
  await waitFor(() =>
    expect(sent("POST")).toContainEqual(["/api/projects/p1/question-sets", { name: "客服 FAQ" }]));
  // The new set is selected: its name renders in the picker.
  expect(await screen.findByText("客服 FAQ")).toBeInTheDocument();
});

test("before any run the launch button says run, not re-run", async () => {
  matrixBody = { runs: [], rows: [] };
  renderWorkbench();
  await screen.findByText("客服常問 20 題");
  expect(await screen.findByRole("button", { name: "執行整組" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "重跑整組" })).not.toBeInTheDocument();
});

test("the selected set can be renamed", async () => {
  renderWorkbench();
  await screen.findByText("客服常問 20 題");
  await userEvent.click(screen.getByRole("button", { name: "重新命名題組" }));
  const dialog = await screen.findByRole("dialog");
  const name = within(dialog).getByLabelText("題組名稱");
  expect(name).toHaveValue("客服常問 20 題");
  await userEvent.clear(name);
  await userEvent.type(name, "客服常問");
  await userEvent.click(within(dialog).getByRole("button", { name: /^儲\s?存$/ }));
  await waitFor(() =>
    expect(sent("PATCH")).toContainEqual(["/api/projects/p1/question-sets/s1", { name: "客服常問" }]));
});

test("archiving a set confirms that its runs stay", async () => {
  renderWorkbench();
  await screen.findByText("客服常問 20 題");
  await userEvent.click(screen.getByRole("button", { name: "封存題組" }));
  expect(await screen.findByText(/過去的執行仍留在評分矩陣/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^封\s?存$/ }));
  await waitFor(() =>
    expect(sent("DELETE")).toContainEqual(["/api/projects/p1/question-sets/s1", null]));
});

test("archiving a question that has runs says its answers are kept", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByText("題目 (20)"));
  await userEvent.click(await screen.findByRole("button", { name: "封存題目:題目 2" }));
  expect(await screen.findByText(/過去的執行仍保留此題的答案/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^封\s?存$/ }));
  await waitFor(() =>
    expect(sent("DELETE")).toContainEqual(["/api/projects/p1/question-sets/s1/questions/q-1", null]));
});

test("a question can be added from the questions list", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByText("題目 (20)"));
  await userEvent.type(await screen.findByPlaceholderText("輸入要加入此題組的題目"), "運費怎麼算?");
  await userEvent.click(screen.getByRole("button", { name: "新增題目" }));
  await waitFor(() =>
    expect(sent("POST")).toContainEqual([
      "/api/projects/p1/question-sets/s1/questions", { text: "運費怎麼算?" },
    ]));
});

test("without project:edit_content the set and question actions are absent", async () => {
  renderWorkbench({ canEdit: false });
  await screen.findByText("客服常問 20 題");
  await userEvent.click(await screen.findByText("題目 (20)"));
  await screen.findByText("1. 題目 1");
  for (const name of ["新增題組", "重新命名題組", "封存題組", "新增題目"]) {
    expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
  }
  expect(screen.queryByRole("button", { name: /^編輯題目:/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /^封存題目:/ })).not.toBeInTheDocument();
});
