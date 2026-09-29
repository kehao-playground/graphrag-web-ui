import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Empty, Input, Select, Slider, Space, Spin, Typography } from "antd";
import Graph from "graphology";
import {
  ControlsContainer, SigmaContainer, useRegisterEvents, useSigma, ZoomControl,
} from "@react-sigma/core";
import { useLayoutForceAtlas2 } from "@react-sigma/layout-forceatlas2";
import "@react-sigma/core/lib/style.css";
import { artifactGraph } from "../api/queries";
import type { GraphData, GraphEdge } from "../api/types";
import ArtifactQueryError from "./ArtifactQueryError";
import { buildGraph, communityLegend } from "./graphBuilder";
import { communityColor } from "./palette";

const EMPTY_DATA: GraphData = {
  level: 0, levels: [], nodes: [], edges: [], stale: false, truncated: false, node_limit: null,
};

// Sigma-ready payload pushed into the long-lived graphology instance whenever
// the data or the filters change (see GraphSync). Node key = title. Search is
// not part of it: a search only re-highlights (SearchHighlight).
interface SyncPayload {
  nodes: { key: string; attrs: { label: string; size: number; color: string } }[];
  edges: GraphEdge[];
}

// Sigma renderer settings, stable across renders. Labels draw only for nodes
// at least this many pixels wide, so the overview shows the hubs' names and
// zooming in reveals the rest instead of one overlapping blanket of text.
const SIGMA_SETTINGS = { labelRenderedSizeThreshold: 12 };

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
// survives filter changes. Nodes arrive un-highlighted; SearchHighlight,
// rendered after this component, re-applies the search in the same commit.
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

// Re-applies the search as the `highlighted` attribute — no re-import, no
// re-layout, so the nodes keep their places while the user types (R1-86).
function SearchHighlight({ payload, needle }: { payload: SyncPayload; needle: string }) {
  const sigma = useSigma();
  // payload is a dependency so a re-layout (which re-imports the nodes
  // un-highlighted) gets the current search re-applied.
  useEffect(() => {
    sigma.getGraph().updateEachNodeAttributes((_key, attrs) => ({
      ...attrs,
      highlighted: needle !== "" && String(attrs.label).toLowerCase().includes(needle),
    }));
    sigma.refresh();
  }, [sigma, payload, needle]);
  return null;
}

// Node click opens the entity's detail (ExplorePanel's drawer); hovering a
// node shows a pointer so the canvas reads as clickable.
function NodeEvents({ onClick }: { onClick: (key: string) => void }) {
  const sigma = useSigma();
  const registerEvents = useRegisterEvents();
  useEffect(() => {
    registerEvents({
      clickNode: (e) => onClick(e.node),
      enterNode: () => { sigma.getContainer().style.cursor = "pointer"; },
      leaveNode: () => { sigma.getContainer().style.cursor = ""; },
    });
  }, [registerEvents, sigma, onClick]);
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
  // Only a focus followed by a clear triggers the overview reset; mounting
  // with no search must leave the camera untouched.
  const hadFocus = useRef(false);
  useEffect(() => {
    if (!target) {
      if (hadFocus.current) {
        // Search cleared: pull the camera back so the focused zoom (one node
        // filling the viewport) doesn't read as "the graph shrank".
        hadFocus.current = false;
        sigma.getCamera().animate({ x: 0.5, y: 0.5, angle: 0, ratio: 1 }, { duration: 500 });
      }
      return;
    }
    const pos = sigma.getNodeDisplayData(target);
    if (!pos) return;
    hadFocus.current = true;
    sigma.getCamera().animate({ x: pos.x, y: pos.y, ratio: 0.3 }, { duration: 500 });
  }, [sigma, target]);
  return null;
}

export default function GraphView({ projectId, canUse = true, onOpenNode }: {
  projectId: string;
  canUse?: boolean;
  onOpenNode?: (hrid: number) => void;
}) {
  const { t } = useTranslation();
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

  // Errors render in place (ArtifactQueryError), so no toast on top.
  const graph = useQuery({ ...artifactGraph(projectId, level), enabled: canUse, meta: { silent: true } });

  const typeOptions = useMemo(() => {
    const distinct = [...new Set((graph.data?.nodes ?? []).map((n) => n.type))];
    return distinct.sort().map((value) => ({ value, label: value }));
  }, [graph.data]);

  // Filter → sigma-ready payload; GraphSync pushes it into the stable graph.
  const { payload, legend } = useMemo(() => {
    const built = buildGraph(graph.data ?? EMPTY_DATA, { minDegree, types });
    const nodes = built.nodes.map((n) => ({
      key: n.title,
      attrs: { label: n.title, size: 4 + Math.sqrt(n.degree) * 2, color: communityColor(n.community) },
    }));
    return { payload: { nodes, edges: built.edges }, legend: communityLegend(built.nodes) };
  }, [graph.data, minDegree, types]);

  const needle = search.trim().toLowerCase();
  const firstMatch = useMemo(
    () => (needle === "" ? null : payload.nodes.find((n) => n.key.toLowerCase().includes(needle))?.key ?? null),
    [payload, needle],
  );

  // Node key = title; the drawer is addressed by human_readable_id.
  const hridByTitle = useMemo(
    () => new Map((graph.data?.nodes ?? []).map((n) => [n.title, n.hrid])),
    [graph.data],
  );
  const openNode = useCallback((key: string) => {
    const hrid = hridByTitle.get(key);
    if (hrid !== undefined) onOpenNode?.(hrid);
  }, [hridByTitle, onOpenNode]);

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {graph.data?.stale && <Alert type="warning" showIcon message={t("explore.staleWarning")} />}
      {graph.data?.truncated && (
        <Alert
          type="info"
          showIcon
          message={t("explore.truncatedWarning", { count: graph.data.node_limit ?? 0 })}
        />
      )}
      <Space wrap>
        <Select
          aria-label={t("explore.columns.level")}
          placeholder={t("explore.columns.level")}
          style={{ width: 120 }}
          disabled={!canUse}
          value={level ?? graph.data?.level}
          options={(graph.data?.levels ?? []).map((v) => ({ value: v, label: String(v) }))}
          onChange={setLevel}
        />
        <Select
          aria-label={t("explore.columns.type")}
          mode="multiple"
          placeholder={t("explore.columns.type")}
          style={{ minWidth: 180 }}
          disabled={!canUse}
          value={types}
          options={typeOptions}
          onChange={setTypes}
        />
        <Slider
          aria-label={t("graph.minDegree")}
          min={0}
          max={10}
          value={minDegreeDraft}
          disabled={!canUse}
          style={{ width: 160, margin: "0 8px" }}
          onChange={(v) => setMinDegreeDraft(v as number)}
          onChangeComplete={(v) => setMinDegree(v as number)}
        />
        <Input.Search
          aria-label={t("graph.searchNodes")}
          placeholder={t("graph.searchNodesPlaceholder")}
          style={{ width: 220 }}
          allowClear
          disabled={!canUse}
          onSearch={setSearch}
        />
      </Space>
      {graph.error ? (
        <ArtifactQueryError error={graph.error} projectId={projectId} />
      ) : graph.isPending ? (
        <Spin style={{ display: "block", marginTop: 64 }} />
      ) : payload.nodes.length === 0 ? (
        <Empty description={t("graph.empty")} />
      ) : (
        <>
          <Space wrap size="middle" role="list" aria-label={t("graph.legend")}>
            {legend.map((entry) => (
              <span role="listitem" key={entry.kind === "community" ? entry.community : entry.kind}>
                <span
                  aria-hidden
                  style={{
                    display: "inline-block", width: 10, height: 10, borderRadius: "50%", marginRight: 6,
                    background: entry.kind === "community" ? communityColor(entry.community)
                      : entry.kind === "none" ? communityColor(null) : "transparent",
                    border: entry.kind === "other" ? "1px dashed #8c8c8c" : undefined,
                  }}
                />
                <Typography.Text type="secondary">
                  {entry.kind === "community"
                    ? t("graph.legendCommunity", { community: entry.community, count: entry.count })
                    : entry.kind === "none"
                      ? t("graph.legendNone", { count: entry.count })
                      : t("graph.legendOther", { count: entry.count })}
                </Typography.Text>
              </span>
            ))}
          </Space>
          <SigmaContainer style={{ height: 640 }} graph={sigmaGraph} settings={SIGMA_SETTINGS}>
            <GraphSync payload={payload} />
            <SearchHighlight payload={payload} needle={needle} />
            <SearchFocus target={firstMatch} />
            <NodeEvents onClick={openNode} />
            <ControlsContainer position="bottom-right">
              <ZoomControl
                labels={{ zoomIn: t("graph.zoomIn"), zoomOut: t("graph.zoomOut"), reset: t("graph.zoomReset") }}
              />
            </ControlsContainer>
          </SigmaContainer>
        </>
      )}
    </Space>
  );
}
