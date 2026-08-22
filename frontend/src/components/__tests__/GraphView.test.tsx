import { render, screen, waitFor, cleanup, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi, beforeEach, afterEach } from "vitest";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import GraphView from "../GraphView";
import { useAuth } from "../../stores/auth";
import type { GraphData } from "../../api/types";

// Structural slice of graphology Graph — enough for assertions without
// importing sigma-side types.
interface SigmaGraph {
  order: number;
  forEachNode: (cb: (key: string, attrs: Record<string, unknown>) => void) => void;
  getNodeAttributes: (key: string) => Record<string, unknown>;
}

// Sigma mocks: SigmaContainer renders a stub div and captures the graphology
// instance passed via the `graph` prop so tests can assert nodes/attrs;
// useSigma serves that graph plus a recording camera. Wrapper v5 ships the
// synchronous useLayoutForceAtlas2 hook (the FA2Layout component was dropped
// upstream), so the layout mock provides a noop assign.
const h = vi.hoisted(() => ({
  lastGraph: null as SigmaGraph | null,
  cameraAnimate: vi.fn(),
}));
vi.mock("@react-sigma/core", () => ({
  SigmaContainer: (props: { graph?: SigmaGraph; children?: ReactNode }) => {
    h.lastGraph = props.graph ?? null;
    return <div data-testid="sigma">{props.children}</div>;
  },
  useSigma: () => ({
    getGraph: () => h.lastGraph,
    getCamera: () => ({ animate: h.cameraAnimate }),
  }),
}));
vi.mock("@react-sigma/layout-forceatlas2", () => ({
  useLayoutForceAtlas2: () => ({ positions: () => ({}), assign: () => undefined }),
}));

// Fixture: degrees 2/1/0 so the default min_degree=1 already filters one node
// and its edge; levels [0, 1] drive the level Select.
const GRAPH: GraphData = {
  level: 1,
  levels: [0, 1],
  stale: false,
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
  h.lastGraph = null;
  h.cameraAnimate.mockClear();
});
beforeEach(() => {
  fetchMock.mockClear();
  vi.stubGlobal("fetch", fetchMock);
  useAuth.setState({ accessToken: "test-token" });
});

function mount() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <GraphView projectId="p1" />
    </QueryClientProvider>,
  );
}

function nodeTitles() {
  const titles: string[] = [];
  h.lastGraph?.forEachNode((key) => titles.push(key));
  return titles.sort();
}

test("initial load fetches the graph without a level param", async () => {
  mount();
  expect(await screen.findByTestId("sigma")).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/p1/artifacts/graph", expect.anything());
  // min_degree default 1 drops the degree-0 node; its dangling edge goes too
  expect(nodeTitles()).toEqual(["Ada Lovelace", "Alan Turing"]);
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

test("min_degree Slider increase passes fewer nodes to sigma", async () => {
  mount();
  await screen.findByTestId("sigma");
  // rc-slider's keyboard handler reads e.which/e.keyCode, which jsdom does
  // not derive from `key` — fireEvent must pass keyCode explicitly.
  fireEvent.keyDown(screen.getByRole("slider"), { key: "ArrowRight", keyCode: 39 });
  // 1 → 2: only Alan Turing (degree 2) survives
  await waitFor(() => expect(nodeTitles()).toEqual(["Alan Turing"]));
});

test("search flags matching nodes highlighted and animates the camera to the first match", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  await user.type(screen.getByRole("searchbox", { name: "搜尋節點" }), "alan{Enter}");
  await waitFor(() => {
    expect(h.lastGraph?.getNodeAttributes("Alan Turing").highlighted).toBe(true);
    expect(h.lastGraph?.getNodeAttributes("Ada Lovelace").highlighted).toBe(false);
  });
  expect(h.cameraAnimate).toHaveBeenCalled();
});

test("filters matching nothing render the empty state instead of sigma", async () => {
  mount();
  await screen.findByTestId("sigma");
  const user = userEvent.setup();
  // ARTIFACT node has degree 0 < min_degree 1 → zero nodes survive
  await user.click(screen.getByRole("combobox", { name: "類型" }));
  await user.click(await screen.findByText("ARTIFACT", { selector: ".ant-select-item-option-content" }));
  expect(await screen.findByText("沒有可顯示的節點")).toBeInTheDocument();
  expect(screen.queryByTestId("sigma")).not.toBeInTheDocument();
});
