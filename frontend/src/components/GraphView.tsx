import { useEffect, useMemo, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Alert, Empty, Input, Select, Slider, Space, Spin, message } from "antd";
import Graph from "graphology";
import { SigmaContainer, useSigma } from "@react-sigma/core";
import { useLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import "@react-sigma/core/lib/style.css";
import { fetchGraph } from "../api/client";
import type { GraphData } from "../api/types";
import { buildGraph } from "./graphBuilder";
import { communityColor } from "./palette";

const EMPTY_DATA: GraphData = { level: 0, levels: [], nodes: [], edges: [], stale: false };

// forceatlas2 layout runner in wrapper-v5 hook form (the FA2Layout component
// no longer exists upstream): the synchronous hook runs 100 iterations on the
// main thread — no web-worker import that could break the Vite build. Re-runs
// whenever SigmaContainer hands out a new sigma instance.
function FA2Layout() {
  const sigma = useSigma();
  const { assign } = useLayoutForceAtlas2({ iterations: 100, settings: { barnesHutOptimize: true } });
  useEffect(() => {
    assign();
  }, [sigma, assign]);
  return null;
}

// Camera-focus helper: animates the camera onto the first search match.
function SearchFocus({ target }: { target: string | null }) {
  const sigma = useSigma();
  useEffect(() => {
    if (!target) return;
    const { x, y } = sigma.getGraph().getNodeAttributes(target);
    sigma.getCamera().animate({ x, y, ratio: 0.3 }, { duration: 500 });
  }, [sigma, target]);
  return null;
}

export default function GraphView({ projectId, canUse = true }: { projectId: string; canUse?: boolean }) {
  const [level, setLevel] = useState<number | undefined>(undefined);
  const [types, setTypes] = useState<string[]>([]);
  const [minDegree, setMinDegree] = useState(1);
  const [search, setSearch] = useState("");

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

  // Filter → graphology graph with sigma-ready attributes. Node key = title.
  // FA2 needs starting positions, so seed random x/y before the layout pass.
  const { sigmaGraph, firstMatch } = useMemo(() => {
    const built = buildGraph(graph.data ?? EMPTY_DATA, { minDegree, types });
    const needle = search.trim().toLowerCase();
    let first: string | null = null;
    // multigraph: the parquet relationships may hold parallel source→target rows
    const g = new Graph({ multi: true });
    for (const n of built.nodes) {
      const highlighted = needle !== "" && n.title.toLowerCase().includes(needle);
      if (highlighted && first === null) first = n.title;
      g.addNode(n.title, {
        label: n.title,
        size: 4 + Math.sqrt(n.degree) * 2,
        color: communityColor(n.community),
        highlighted,
        x: Math.random(),
        y: Math.random(),
      });
    }
    for (const e of built.edges) g.addEdge(e.source, e.target);
    return { sigmaGraph: g, firstMatch: first };
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
          defaultValue={1}
          disabled={!canUse}
          style={{ width: 160, margin: "0 8px" }}
          onChange={(v) => setMinDegree(v as number)}
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
      ) : sigmaGraph.order === 0 ? (
        <Empty description="沒有可顯示的節點" />
      ) : (
        <SigmaContainer style={{ height: 640 }} graph={sigmaGraph}>
          <FA2Layout />
          <SearchFocus target={firstMatch} />
        </SigmaContainer>
      )}
    </Space>
  );
}
