import { act, render, screen, waitFor, cleanup, fireEvent, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter } from "react-router-dom";
import GraphView from "../GraphView";
import { useAuth } from "../../stores/auth";
import type { GraphData } from "../../api/types";

// Structural slice of graphology Graph — the component owns the REAL
// instance (stable-ref fix), tests only need the read API for assertions.
interface SigmaGraph {
  order: number;
  clear: () => void;
  forEachNode: (cb: (key: string, attrs: Record<string, unknown>) => void) => void;
  getNodeAttributes: (key: string) => Record<string, unknown>;
}

// Sigma mocks: SigmaContainer is a passthrough that renders children and
// records every `graph` prop it receives — the blank-canvas fix under test
// keeps that prop referentially stable. useSigma hands the real graphology
// instance back (GraphView creates it and passes it through the container)
// plus a refresh spy and a recording camera; the stub object is created once
// so `sigma` keeps a stable identity across renders, like the real thing.
// The layout mock records assign calls instead of running FA2.
const h = vi.hoisted(() => {
  const graphs: unknown[] = [];
  const refresh = vi.fn();
  const cameraAnimate = vi.fn();
  const assign = vi.fn();
  // Fixed display-space coords: the camera must receive THESE, never the
  // node's raw graph-space x/y (random FA2 seeds can never equal them).
  const getNodeDisplayData = vi.fn((): { x: number; y: number } | undefined => ({ x: 0.42, y: 0.58 }));
  const container = { style: { cursor: "" } };
  return {
    graphs,
    refresh,
    cameraAnimate,
    assign,
    getNodeDisplayData,
    container,
    events: {} as Record<string, (e: { node: string }) => void>,
    settings: [] as unknown[],
    sigma: {
      getGraph: () => graphs[graphs.length - 1],
      getNodeDisplayData,
      getCamera: () => ({ animate: cameraAnimate }),
      getContainer: () => container,
      refresh,
    },
  };
});
vi.mock("@react-sigma/core", () => ({
  SigmaContainer: (props: { graph?: unknown; settings?: unknown; children?: ReactNode }) => {
    h.graphs.push(props.graph);
    h.settings.push(props.settings);
    return <div data-testid="sigma">{props.children}</div>;
  },
  ControlsContainer: (props: { children?: ReactNode }) => <div data-testid="controls">{props.children}</div>,
  ZoomControl: (props: { labels?: Record<string, string> }) => (
    <>{Object.values(props.labels ?? {}).map((l) => <button key={l} type="button">{l}</button>)}</>
  ),
  useSigma: () => h.sigma,
  useRegisterEvents: () => (handlers: Record<string, (e: { node: string }) => void>) => {
    h.events = handlers;
  },
}));
vi.mock("@react-sigma/layout-forceatlas2", () => ({
  useLayoutForceAtlas2: () => ({ positions: () => ({}), assign: h.assign }),
}));

// Fixture: degrees 2/1/0 so the default min_degree=1 already filters one node
// and its edge; levels [0, 1] drive the level Select.
const GRAPH: GraphData = {
  level: 1,
  levels: [0, 1],
  stale: false,
  truncated: false,
  node_limit: null,
  nodes: [
    { hrid: 1, title: "Alan Turing", type: "PERSON", degree: 2, frequency: 3, community: 0 },
    { hrid: 2, title: "Ada Lovelace", type: "PERSON", degree: 1, frequency: 2, community: 1 },
    { hrid: 3, title: "Analytical Engine", type: "ARTIFACT", degree: 0, frequency: 2, community: null },
  ],
  edges: [
    { source: "Alan Turing", target: "Ada Lovelace", weight: 4 },
    { source: "Ada Lovelace", target: "Analytical Engine", weight: 1 },
  ],
};

const fetchMock = vi.fn(async () => new Response(JSON.stringify(GRAPH), { status: 200 }));

afterEach(() => {
  vi.unstubAllGlobals();
  cleanup();
  // Drop lingering select dropdowns so texts don't collide across tests.
  document.querySelectorAll(".ant-select-dropdown").forEach((el) => el.remove());
  h.graphs.length = 0;
  h.refresh.mockClear();
  h.cameraAnimate.mockClear();
  h.getNodeDisplayData.mockClear();
  h.settings.length = 0;
  h.events = {};
});
beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  useAuth.setState({ accessToken: "test-token" });
});

function mount(onOpenNode?: (hrid: number) => void) {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <GraphView projectId="p1" onOpenNode={onOpenNode} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function sigmaGraph(): SigmaGraph | null {
  return (h.graphs[h.graphs.length - 1] as SigmaGraph | undefined) ?? null;
}

function nodeTitles() {
  const titles: string[] = [];
  sigmaGraph()?.forEachNode((key) => titles.push(key));
  return titles.sort();
}

test("initial load fetches the graph without a level param", async () => {
  mount();
  expect(await screen.findByTestId("sigma")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/graph", expect.anything());
  // min_degree default 1 drops the degree-0 node; its dangling edge goes too
  expect(nodeTitles()).toEqual(["Ada Lovelace", "Alan Turing"]);
  // the initial in-place sync ran FA2 and refreshed sigma
  expect(h.assign).toHaveBeenCalled();
  expect(h.refresh).toHaveBeenCalled();
});

test("stale=true renders the indexing alert above the graph", async () => {
  fetchMock.mockImplementationOnce(
    async () => new Response(JSON.stringify({ ...GRAPH, stale: true }), { status: 200 }));
  mount();
  expect(await screen.findByText("索引進行中，結果可能不完整")).toBeInTheDocument();
});

test("truncated=true tells the reader the graph was capped", async () => {
  // A capped graph that says nothing is worse than a capped graph: the
  // reader has no way to tell a sparse corpus from a cut-off one.
  fetchMock.mockImplementationOnce(
    async () =>
      new Response(JSON.stringify({ ...GRAPH, truncated: true, node_limit: 2000 }), { status: 200 }));
  mount();
  expect(await screen.findByText(/2000/)).toBeInTheDocument();
});

test("truncated=false renders no cap notice", async () => {
  mount();
  await screen.findByTestId("sigma");
  expect(screen.queryByText(/已被截斷/)).not.toBeInTheDocument();
});

test("level Select change refetches with level=0", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "層級" }));
  await user.click(await screen.findByText("0", { selector: ".ant-select-item-option-content" }));
  await waitFor(() =>
    expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/graph?level=0", expect.anything()));
});

test("graph prop stays referentially stable across search, slider and type changes", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox", { name: "搜尋節點" }), "alan{Enter}");
  const slider = screen.getByRole("slider");
  fireEvent.keyDown(slider, { key: "ArrowRight", keyCode: 39 });
  fireEvent.keyUp(slider, { key: "ArrowRight", keyCode: 39 });
  await waitFor(() => expect(sigmaGraph()?.order).toBe(1));
  await user.click(screen.getByRole("combobox", { name: "類型" }));
  // PERSON keeps the degree-2 survivor, so sigma stays mounted throughout
  await user.click(await screen.findByText("PERSON", { selector: ".ant-select-item-option-content" }));
  await waitFor(() => expect(sigmaGraph()?.order).toBe(1));
  // Many SigmaContainer re-renders happened, yet every `graph` prop was the
  // SAME instance: the kill/re-create/camera-carry path is never taken.
  expect(h.graphs.length).toBeGreaterThan(1);
  for (const g of h.graphs) expect(g).toBe(h.graphs[0]);
});

test("min_degree Slider commits on release: keyDown previews, keyUp applies", async () => {
  mount();
  await screen.findByTestId("sigma");
  const slider = screen.getByRole("slider");
  const fetches = fetchMock.mock.calls.length;
  const syncs = h.assign.mock.calls.length;
  // Drag tick. rc-slider fires onChange per keyDown but commits only on
  // keyUp; its keyboard handler reads e.which/e.keyCode, which jsdom does
  // not derive from `key` — fireEvent must pass keyCode explicitly.
  fireEvent.keyDown(slider, { key: "ArrowRight", keyCode: 39 });
  await waitFor(() => expect(slider).toHaveAttribute("aria-valuenow", "2"));
  // The handle moved to 2, but the committed min_degree still filters at 1:
  // no payload rebuild, no refetch, no sigma re-sync during the drag.
  expect(nodeTitles()).toEqual(["Ada Lovelace", "Alan Turing"]);
  expect(fetchMock.mock.calls.length).toBe(fetches);
  expect(h.assign.mock.calls.length).toBe(syncs);
  // Release: onChangeComplete commits min_degree 1 → 2, dropping Ada
  // (degree 1) and re-syncing sigma's graph in place — still client-side only.
  fireEvent.keyUp(slider, { key: "ArrowRight", keyCode: 39 });
  await waitFor(() => expect(nodeTitles()).toEqual(["Alan Turing"]));
  expect(sigmaGraph()?.order).toBe(1);
  expect(fetchMock.mock.calls.length).toBe(fetches);
  expect(h.assign.mock.calls.length).toBe(syncs + 1);
});

test("search only re-highlights: no clear, no re-seed, no FA2 re-run (R1-86)", async () => {
  mount();
  await screen.findByTestId("sigma");
  const syncs = h.refresh.mock.calls.length;
  const layouts = h.assign.mock.calls.length;
  const g = sigmaGraph()!;
  const clearSpy = vi.spyOn(g, "clear");
  const before = { ...g.getNodeAttributes("Alan Turing") };
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox", { name: "搜尋節點" }), "alan{Enter}");
  await waitFor(() => expect(h.refresh.mock.calls.length).toBe(syncs + 1));
  // The layout is untouched: same positions, no clear, FA2 not re-run.
  expect(clearSpy).not.toHaveBeenCalled();
  expect(h.assign.mock.calls.length).toBe(layouts);
  expect(g.getNodeAttributes("Alan Turing")).toMatchObject({ x: before.x, y: before.y, highlighted: true });
  expect(g.getNodeAttributes("Ada Lovelace")).toMatchObject({ highlighted: false });
  // Camera x/y live in normalized display space: the animate target must be
  // the coords from sigma.getNodeDisplayData, never the node's raw
  // graph-space x/y (which would fly the camera off-canvas).
  expect(h.cameraAnimate).toHaveBeenCalledWith({ x: 0.42, y: 0.58, ratio: 0.3 }, { duration: 500 });
});

test("a filter change keeps the current search highlighted after the re-layout", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox", { name: "搜尋節點" }), "alan{Enter}");
  await waitFor(() => expect(sigmaGraph()!.getNodeAttributes("Alan Turing").highlighted).toBe(true));
  await user.click(screen.getByRole("combobox", { name: "類型" }));
  await user.click(await screen.findByText("PERSON", { selector: ".ant-select-item-option-content" }));
  await waitFor(() => expect(h.assign.mock.calls.length).toBeGreaterThan(1));
  expect(sigmaGraph()!.getNodeAttributes("Alan Turing").highlighted).toBe(true);
});

test("the level Select shows the level the server picked (R4-39)", async () => {
  mount();
  await screen.findByTestId("sigma");
  const level = screen.getByRole("combobox", { name: "層級" }).closest(".ant-select") as HTMLElement;
  expect(level.querySelector(".ant-select-content")?.textContent ?? level.textContent).toContain("1");
});

test("a legend names the communities on screen with their colours (R4-39)", async () => {
  mount();
  await screen.findByTestId("sigma");
  const legend = screen.getByRole("list", { name: "社群圖例" });
  const items = within(legend).getAllByRole("listitem").map((li) => li.textContent);
  expect(items).toEqual(["社群 0（1）", "社群 1（1）"]);
});

test("the legend folds communities past the top eight into 'other'", async () => {
  const many: GraphData = {
    ...GRAPH,
    nodes: Array.from({ length: 10 }, (_, i) => ({
      hrid: i, title: `N${i}`, type: "PERSON", degree: 2, frequency: 1, community: i,
    })),
    edges: [],
  };
  fetchMock.mockImplementationOnce(async () => new Response(JSON.stringify(many), { status: 200 }));
  mount();
  await screen.findByTestId("sigma");
  const items = within(screen.getByRole("list", { name: "社群圖例" })).getAllByRole("listitem");
  expect(items).toHaveLength(9);
  expect(items[8].textContent).toBe("其他社群（2）");
});

test("clicking a node hands its human-readable id to onOpenNode (R4-39)", async () => {
  const onOpenNode = vi.fn();
  mount(onOpenNode);
  await screen.findByTestId("sigma");
  act(() => h.events.clickNode({ node: "Ada Lovelace" }));
  expect(onOpenNode).toHaveBeenCalledWith(2);
  act(() => h.events.enterNode({ node: "Ada Lovelace" }));
  expect(h.container.style.cursor).toBe("pointer");
  act(() => h.events.leaveNode({ node: "Ada Lovelace" }));
  expect(h.container.style.cursor).toBe("");
});

test("zoom controls are labelled and labels hide below a size threshold (R4-39)", async () => {
  mount();
  await screen.findByTestId("sigma");
  expect(screen.getByRole("button", { name: "放大" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "縮小" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重設視角" })).toBeInTheDocument();
  expect(h.settings[h.settings.length - 1]).toMatchObject({ labelRenderedSizeThreshold: expect.any(Number) });
});

test("not_indexed renders the catalogued sentence with a link to Jobs (R4-15)", async () => {
  fetchMock.mockImplementationOnce(async () =>
    new Response(JSON.stringify({ detail: "not indexed", code: "not_indexed" }), { status: 409 }));
  mount();
  expect(await screen.findByText("尚未建立索引，請先執行索引任務")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "前往任務" })).toHaveAttribute("href", "/projects/p1/jobs");
  expect(screen.queryByTestId("sigma")).not.toBeInTheDocument();
});

test("camera animation is skipped when the match has no display data", async () => {
  mount();
  await screen.findByTestId("sigma");
  // e.g. the target got filtered out between render and focus: no crash,
  // no camera move.
  h.getNodeDisplayData.mockReturnValueOnce(undefined);
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox", { name: "搜尋節點" }), "ada{Enter}");
  await waitFor(() => expect(h.getNodeDisplayData).toHaveBeenCalled());
  expect(h.cameraAnimate).not.toHaveBeenCalled();
});

test("clearing the search after a focus resets the camera to overview", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  const box = screen.getByRole("searchbox", { name: "搜尋節點" });
  await user.type(box, "alan{Enter}");
  expect(h.cameraAnimate).toHaveBeenCalledWith({ x: 0.42, y: 0.58, ratio: 0.3 }, { duration: 500 });
  h.cameraAnimate.mockClear();
  // allowClear X: search goes back to "" → target null after a real focus
  await user.click(box.parentElement!.querySelector(".ant-input-clear-icon")!);
  await waitFor(() =>
    expect(h.cameraAnimate).toHaveBeenCalledWith({ x: 0.5, y: 0.5, angle: 0, ratio: 1 }, { duration: 500 }));
});

test("initial load with no search never moves the camera", async () => {
  mount();
  await screen.findByTestId("sigma");
  // mount-with-null must not animate — only a focus followed by a clear does
  expect(h.getNodeDisplayData).not.toHaveBeenCalled();
  expect(h.cameraAnimate).not.toHaveBeenCalled();
});

test("filters matching nothing render the empty state instead of sigma", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  // ARTIFACT node has degree 0 < min_degree 1 → zero nodes survive
  await user.click(screen.getByRole("combobox", { name: "類型" }));
  await user.click(await screen.findByText("ARTIFACT", { selector: ".ant-select-item-option-content" }));
  // The empty state names the filters as the cause (R4-30).
  expect(await screen.findByText("沒有節點符合目前的篩選條件，請降低最小連結數或清除類型篩選。")).toBeInTheDocument();
  expect(screen.queryByTestId("sigma")).not.toBeInTheDocument();
});
