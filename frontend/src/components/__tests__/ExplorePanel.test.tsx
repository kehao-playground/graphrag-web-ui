import { render, screen, waitFor, cleanup, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ExplorePanel from "../ExplorePanel";
import { useAuth } from "../../stores/auth";
import type { GraphData } from "../../api/types";

// GraphView (圖譜 mode) pulls in sigma: stub the WebGL layer so jsdom never
// touches canvas. GraphView.test.tsx covers the graph in depth.
vi.mock("@react-sigma/core", () => ({
  SigmaContainer: (props: { children?: ReactNode }) => <div data-testid="sigma">{props.children}</div>,
  useSigma: () => ({
    getGraph: () => ({ getNodeAttributes: () => ({}) }),
    getCamera: () => ({ animate: vi.fn() }),
  }),
}));
vi.mock("@react-sigma/layout-forceatlas2", () => ({
  useLayoutForceAtlas2: () => ({ positions: () => ({}), assign: () => undefined }),
}));
// Row fixtures shaped like the backend list projection (Task 1 list_columns
// — no description/text columns) and the full detail row (SELECT *).
const ROWS: Record<string, unknown>[] = [
  { human_readable_id: 1, title: "Alan Turing", type: "PERSON", frequency: 3, degree: 2 },
  { human_readable_id: 2, title: "Ada Lovelace", type: "PERSON", frequency: 2, degree: 1 },
  { human_readable_id: 3, title: "Analytical Engine", type: "ARTIFACT", frequency: 2, degree: 0 },
];
const DETAIL_ROW = {
  id: "e2",
  human_readable_id: 2,
  title: "Ada Lovelace",
  type: "PERSON",
  frequency: 2,
  degree: 1,
  description: "first programmer",
  text_unit_ids: ["tu-1"],
};
const OTHER_TABLES: Record<string, Record<string, unknown>[]> = {
  relationships: [{ human_readable_id: 1, source: "Alan Turing", target: "Ada Lovelace", weight: 4, combined_degree: 2 }],
  communities: [{ human_readable_id: 0, community: 0, level: 1, parent: -1, size: 3, title: "C0" }],
  community_reports: [{ human_readable_id: 0, community: 0, level: 1, rank: 1.5, title: "C0 report" }],
  text_units: [{ human_readable_id: 1, n_tokens: 42, document_id: "doc-1.md" }],
  documents: [{ human_readable_id: 1, title: "doc-1.md", creation_date: "2026-08-01" }],
};

// URL-routing fetch mock (QueryPanel stubGlobal style): the real api() wrapper
// runs on top, so auth headers, URLSearchParams ordering and the {"detail"}
// extraction in client.ts are exercised; branch by route (before "?") so a
// wrong endpoint or query string cannot silently pass.
let listEnvelope = { rows: ROWS, total: ROWS.length, stale: false };
let errorResponse: Response | null = null;
const GRAPH: GraphData = {
  level: 1,
  levels: [0, 1],
  stale: false,
  nodes: [
    { hrid: 1, title: "Alan Turing", type: "PERSON", degree: 2, frequency: 3, community: 0 },
    { hrid: 2, title: "Ada Lovelace", type: "PERSON", degree: 1, frequency: 2, community: 1 },
  ],
  edges: [{ source: "Alan Turing", target: "Ada Lovelace", weight: 4 }],
};
const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
  if (errorResponse) return errorResponse;
  const route = String(input).split("?")[0];
  if (route === "/api/projects/p1/artifacts/entities/2") {
    return new Response(JSON.stringify({ row: DETAIL_ROW, stale: false }), { status: 200 });
  }
  if (route === "/api/projects/p1/artifacts/entities") {
    return new Response(JSON.stringify(listEnvelope), { status: 200 });
  }
  if (route === "/api/projects/p1/artifacts/graph") {
    return new Response(JSON.stringify(GRAPH), { status: 200 });
  }
  const rows = OTHER_TABLES[route.slice("/api/projects/p1/artifacts/".length)];
  if (rows) return new Response(JSON.stringify({ rows, total: rows.length, stale: false }), { status: 200 });
  return new Response(JSON.stringify({}), { status: 200 });
});

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  // Keep the static .ant-message holder alive (JobsPanel test rationale);
  // drop lingering notices/portals so texts don't collide across tests.
  document.querySelectorAll(".ant-message-notice, .ant-drawer-root, .ant-select-dropdown").forEach((el) => el.remove());
});
beforeEach(() => {
  listEnvelope = { rows: ROWS, total: ROWS.length, stale: false };
  errorResponse = null;
  vi.stubGlobal("fetch", fetchMock);
  useAuth.setState({ accessToken: "test-token" });
});

function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ExplorePanel projectId="p1" canUse />
    </QueryClientProvider>,
  );
}
// antd 6 Selects render a hidden a11y mirror listbox (role=option, height 0)
// beside the visible items; clicking the mirror does nothing. Always target
// the visible .ant-select-item-option-content instead.
async function pickOption(user: ReturnType<typeof userEvent.setup>, label: string) {
  await user.click(await screen.findByText(label, { selector: ".ant-select-item-option-content" }));
}

test("renders Segmented 圖譜|資料表; default 資料表 fetches entities page 0", async () => {
  mount();
  expect(screen.getByText("圖譜")).toBeInTheDocument();
  expect(screen.getByText("資料表")).toBeInTheDocument();
  await screen.findByText("Alan Turing");
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/entities?limit=50&offset=0", expect.anything());
});

test("圖譜 mode renders the WebGL graph and fetches the graph endpoint", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByText("Alan Turing");
  await user.click(screen.getByText("圖譜"));
  expect(await screen.findByTestId("sigma")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/graph", expect.anything());
});

test("table switch refetches the new table; q search adds q= and resets offset", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByText("Alan Turing");
  await user.type(screen.getByRole("searchbox", { name: "搜尋" }), "turing{Enter}");
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/entities?limit=50&offset=0&q=turing", expect.anything()));
  // entities carry both filter flags
  expect(screen.getByRole("combobox", { name: "類型" })).toBeInTheDocument();
  expect(screen.getByRole("spinbutton", { name: "社群" })).toBeInTheDocument();
  await user.click(screen.getByRole("combobox", { name: "資料表" }));
  await pickOption(user, "關係");
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/relationships?limit=50&offset=0&q=turing", expect.anything()));
});

test("community filter renders for entities/communities/community_reports only; type for entities only", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByText("Alan Turing");
  const tableSelect = screen.getByRole("combobox", { name: "資料表" });

  await user.click(tableSelect);
  await pickOption(user, "關係");
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/relationships?limit=50&offset=0", expect.anything()));
  expect(screen.queryByRole("combobox", { name: "類型" })).not.toBeInTheDocument();
  expect(screen.queryByRole("spinbutton", { name: "社群" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("combobox", { name: "資料表" }));
  await pickOption(user, "社群");
  await screen.findByText("C0");
  expect(screen.getByRole("spinbutton", { name: "社群" })).toBeInTheDocument();
  expect(screen.queryByRole("combobox", { name: "類型" })).not.toBeInTheDocument();

  await user.click(screen.getByRole("combobox", { name: "資料表" }));
  await pickOption(user, "社群報告");
  await screen.findByText("C0 report");
  expect(screen.getByRole("spinbutton", { name: "社群" })).toBeInTheDocument();

  await user.click(screen.getByRole("combobox", { name: "資料表" }));
  await pickOption(user, "文件");
  await screen.findByText("doc-1.md");
  expect(screen.queryByRole("spinbutton", { name: "社群" })).not.toBeInTheDocument();
});

test("server pagination: page 2 requests offset = pageSize", async () => {
  listEnvelope = { rows: ROWS, total: 51, stale: false };
  mount();
  const user = userEvent.setup();
  await screen.findByText("Alan Turing");
  // Shrink to 1/page via the size changer, then click page 2 → offset 1.
  const pager = within(document.querySelector(".ant-pagination") as HTMLElement);
  await user.click(await screen.findByText("50 / page"));
  await pickOption(user, "1 / page");
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/entities?limit=1&offset=0", expect.anything()));
  await user.click(pager.getByText("2"));
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/entities?limit=1&offset=1", expect.anything()));
});

test("stale=true shows the indexing alert at panel top", async () => {
  listEnvelope = { rows: ROWS, total: 3, stale: true };
  mount();
  expect(await screen.findByText("索引進行中,結果可能不完整")).toBeInTheDocument();
});

test("row click opens the detail drawer and fetches the full row", async () => {
  mount();
  const user = userEvent.setup();
  await screen.findByText("Ada Lovelace");
  await user.click(screen.getByText("Ada Lovelace"));
  expect(await screen.findByText("first programmer")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/entities/2", expect.anything());
});

test("404 unknown table surfaces the backend detail", async () => {
  errorResponse = new Response(JSON.stringify({ detail: "未知的資料表" }), { status: 404 });
  mount();
  expect(await screen.findByText("未知的資料表")).toBeInTheDocument();
});

test("409 not indexed surfaces the backend detail", async () => {
  errorResponse = new Response(JSON.stringify({ detail: "尚未建立索引,請先執行索引任務" }), { status: 409 });
  mount();
  expect(await screen.findByText("尚未建立索引,請先執行索引任務")).toBeInTheDocument();
});
