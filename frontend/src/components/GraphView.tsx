import { useEffect, useMemo, useRef, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Alert, Empty, Input, Select, Slider, Space, Spin, message } from "antd";
import Graph from "graphology";
import { SigmaContainer, useSigma } from "@react-sigma/core";
import { useLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import "@react-sigma/core/lib/style.css";
import { fetchGraph } from "../api/client";
import type { GraphData, GraphEdge } from "../api/types";
import { buildGraph } from "./graphBuilder";
import { communityColor } from "./palette";

const EMPTY_DATA: GraphData = { level: 0, levels: [], nodes: [], edges: [], stale: false };

// Sigma-ready payload pushed into the long-lived graphology instance whenever
// filters or search change (see GraphSync). Node key = title.
interface SyncPayload {
  nodes: { key: string; attrs: { label: string; size: number; color: string; highlighted: boolean } }[];
  edges: GraphEdge[];
}

// In-place graph syncer. @react-sigma v5's SigmaContainer kills and
// re-creates the sigma instance whenever the `graph` prop identity changes,
// and the replacement inherits camera state read from the already-killed
// instance — a dirty camera that leaves the WebGL canvas silently blank.
// GraphView therefore never swaps the graph prop; instead this component
// clears sigma's graph, re-imports the current payload, then runs FA2 (the
// wrapper-v5 hook form — the FA2Layout component no longer exists upstream;
// 100 synchronous iterations on the main thread, no web-worker import that
// could break the Vite build) and refreshes, all in one effect. Side
// benefit: the sigma instance — and with it the user's camera — now
// survives filter changes.
function GraphSync({ payload }: { payload: SyncPayload }) {
  const sigma = useSigma();
  const { assign } = useLayoutForceAtlas2({ iterations: 100, settings: { barnesHutOptimize: true } });
  useEffect(() => {
    const g = sigma.getGraph();
    g.clear();
    // FA2 needs starting positions, so seed random x/y before the layout pass.
    for (const n of payload.nodes) g.addNode(n.key, { ...n.attrs, x: Math.random(), y: Math.random() });
    // multigraph: the parquet relationships may hold parallel source→target rows
    for (const e of payload.edges) g.addEdge(e.source, e.target);
    assign();
    sigma.refresh();
  }, [sigma, payload, assign]);
  return null;
}

// Camera-focus helper: animates the camera onto the first search match.
// Camera x/y live in normalized display space, NOT graph space — feeding the
// node's raw coordinates would fly the camera off-canvas and silently blank
// the view, so the target is resolved through sigma.getNodeDisplayData.
// That returns undefined when the node has been filtered out mid-flight:
// skip the animation instead of throwing. The camera survives filter changes
// (see GraphSync), so the animation starts from wherever the user left it.
function SearchFocus({ target }: { target: string | null }) {
  const sigma = useSigma();
  useEffect(() => {
    if (!target) return;
    const pos = sigma.getNodeDisplayData(target);
    if (!pos) return;
    sigma.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 500 });
  }, [sigma, target]);
  return null;
}

export default function GraphView({ projectId, canUse = true }: { projectId: string; canUse?: boolean }) {
  const [level, setLevel] = useState<number | undefined>(undefined);
  const [types, setTypes] = useState<string[]>([]);
  // Draft = what the slider handle shows mid-drag; minDegree = the committed
  // value that drives the payload rebuild + FA2 layout. Committing only
  // on release keeps dragging cheap (handle re-render, no graph recompute).
  const [minDegree, setMinDegree] = useState(1);
  const [minDegreeDraft, setMinDegreeDraft] = useState(1);
  const [search, setSearch] = useState("");

  // ONE graphology instance for the component's whole lifetime: its stable
  // identity is what keeps SigmaContainer from ever taking the
  // kill/re-create/camera-carry path (see GraphSync). Created lazily on the
  // first render — plain graphology, so jsdom and the browser are both safe.
  const graphRef = useRef<Graph | null>(null);
  if (graphRef.current === null) graphRef.current = new Graph({ multi: true });
  const sigmaGraph = graphRef.current;

  const graph = useQuery({
    queryKey: ["graph", projectId, level],
    queryFn: () => fetchGraph(projectId, level),
    enabled: canUse,
    placeholderData: keepPreviousData,
    retry: false,
  });

  useEffect(() => {
    if (graph.error) message.error(graph.error.message);
  }, [graph.error]);

  const typeOptions = useMemo(() => {
    const distinct = [...new Set((graph.data?.nodes ?? []).map((n) => n.type))];
    return distinct.sort().map((value) => ({ value, label: value }));
  }, [graph.data]);

  // Filter → sigma-ready payload; GraphSync pushes it into the stable graph.
  const { payload, firstMatch } = useMemo(() => {
    const built = buildGraph(graph.data ?? EMPTY_DATA, { minDegree, types });
    const needle = search.trim().toLowerCase();
    let first: string | null = null;
    const nodes = built.nodes.map((n) => {
      const highlighted = needle !== "" && n.title.toLowerCase().includes(needle);
      if (highlighted && first === null) first = n.title;
      return {
        key: n.title,
        attrs: {
          label: n.title,
          size: 4 + Math.sqrt(n.degree) * 2,
          color: communityColor(n.community),
          highlighted,
        },
      };
    });
    return { payload: { nodes, edges: built.edges }, firstMatch: first };
  }, [graph.data, minDegree, types, search]);

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {graph.data?.stale && <Alert type="warning" showIcon message="索引進行中,結果可能不完整" />}
      <Space wrap>
        <Select
          aria-label="層級"
          placeholder="層級"
          style={{ width: 120 }}
          allowClear
          disabled={!canUse}
          value={level}
          options={(graph.data?.levels ?? []).map((v) => ({ value: v, label: String(v) }))}
          onChange={setLevel}
        />
        <Select
          aria-label="類型"
          mode="multiple"
          placeholder="類型"
          style={{ minWidth: 180 }}
          disabled={!canUse}
          value={types}
          options={typeOptions}
          onChange={setTypes}
        />
        <Slider
          aria-label="最小度"
          min={0}
          max={10}
          value={minDegreeDraft}
          disabled={!canUse}
          style={{ width: 160, margin: "0 8px" }}
          onChange={(v) => setMinDegreeDraft(v as number)}
          onChangeComplete={(v) => setMinDegree(v as number)}
        />
        <Input.Search
          aria-label="搜尋節點"
          placeholder="搜尋節點名稱"
          style={{ width: 220 }}
          allowClear
          disabled={!canUse}
          onSearch={setSearch}
        />
      </Space>
      {graph.isPending ? (
        <Spin style={{ display: "block", marginTop: 64 }} />
      ) : payload.nodes.length === 0 ? (
        <Empty description="沒有可顯示的節點" />
      ) : (
        <SigmaContainer style={{ height: 640 }} graph={sigmaGraph}>
          <GraphSync payload={payload} />
          <SearchFocus target={firstMatch} />
        </SigmaContainer>
      )}
    </Space>
  );
}
