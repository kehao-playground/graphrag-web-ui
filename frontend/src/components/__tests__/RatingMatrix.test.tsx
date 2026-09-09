import { render, screen, within, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import { Modal } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Workbench from "../tests/Workbench";
import RatingMatrix from "../tests/RatingMatrix";
import type * as ApiClient from "../../api/client";
import { MATRIX, SETS, QUESTIONS, PREFLIGHT } from "./workbenchFixtures";

// Same mock discipline as FilesPanel/JobsPanel tests: branch by URL so a
// wrong endpoint cannot silently pass on another call's payload. Real
// bodyOf/detailOf stay under test; only the transport is mocked.
let preflightBody: Record<string, unknown> = PREFLIGHT;
const apiMock = vi.fn(async (path: string) => {
  if (path === "/api/projects/p1/question-sets") return json(SETS);
  if (path === "/api/projects/p1/question-sets/s1/questions") return json(QUESTIONS);
  if (path === "/api/projects/p1/test-runs") return json(MATRIX);
  if (path === "/api/projects/p1/jobs/preflight") return json(preflightBody);
  return json({});
});
function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200 });
}
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string]),
}));

// Modal.confirm portals live outside the React tree RTL unmounts; purge
// them between tests (JobsPanel.test pattern).
afterEach(() => {
  Modal.destroyAll();
  cleanup();
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

test("matrix renders one row per lineage and one column per run", async () => {
  renderWorkbench();
  expect(await screen.findByText("Q1 保固期多長")).toBeInTheDocument();
  expect(screen.getAllByRole("columnheader")).toHaveLength(1 + 4); // question + 4 runs
  // Rated cells render the localized score, not the raw enum value.
  expect(screen.getAllByText("良好").length).toBeGreaterThan(0);
});

test("a cell a run never asked renders as not-run, not as an empty answer", async () => {
  renderWorkbench();
  const row = (await screen.findByText("Q5 企業採購窗口")).closest("tr")!;
  // run-1 never asked (null cell) + run-4 cancelled before asking
  // (unfilled placeholder) — both are 未執行, neither is an answer.
  expect(within(row).getAllByLabelText("未執行")).toHaveLength(2);
});

test("an errored cell is marked 錯誤, not shown as an unrated answer", async () => {
  renderWorkbench();
  const row = (await screen.findByText("Q7 發票怎麼開")).closest("tr")!;
  expect(within(row).getByText("錯誤")).toBeInTheDocument();
  expect(within(row).queryByText("未評分")).not.toBeInTheDocument();
});

test("regressions-only uses the same definition the backend reports", async () => {
  renderWorkbench();
  await userEvent.click(await screen.findByLabelText("只看退步的"));
  // Q3: previous run good → newest poor (a regression). Q1 stayed good.
  expect(screen.getByText("Q3 退貨流程幾天")).toBeInTheDocument();
  expect(screen.queryByText("Q1 保固期多長")).not.toBeInTheDocument();
});

test("cells report (run, row, cell) through onCell for the drawer", async () => {
  const onCell = vi.fn();
  render(
    <RatingMatrix
      runs={MATRIX.runs}
      rows={MATRIX.rows}
      regressionsOnly={false}
      onRegressionsOnly={() => {}}
      onCell={onCell}
    />,
  );
  // Q3's newest cell is the poor rating.
  await userEvent.click(screen.getByText("不佳"));
  expect(onCell).toHaveBeenCalledWith(MATRIX.runs[3], MATRIX.rows[1], MATRIX.rows[1].cells[3]);
});

test("completed cells are pickable by question × column; cancelled ones are not", () => {
  render(
    <RatingMatrix
      runs={MATRIX.runs}
      rows={MATRIX.rows}
      regressionsOnly={false}
      onRegressionsOnly={() => {}}
      onCell={() => {}}
    />,
  );
  // Selection (Task 8) aims at question × run, so every completed cell is
  // labelled with both (spec §9.2 "Cell → drawer").
  expect(screen.getByLabelText("Q3 退貨流程幾天 × #13")).toBeInTheDocument();
  // The run-4 placeholder was cancelled before asking: not an answer, and
  // nothing for a drawer or diff to show → not pickable.
  expect(screen.queryByLabelText("Q5 企業採購窗口 × #14")).not.toBeInTheDocument();
});
