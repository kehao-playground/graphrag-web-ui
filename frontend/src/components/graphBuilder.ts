import type { GraphData, GraphEdge, GraphNode } from "../api/types";

export interface BuildGraphOptions {
  minDegree: number;
  types: string[];
}

export interface BuiltGraph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

// Pure, sigma-free filter pass (the only graph logic testable without
// WebGL): drop nodes below the degree floor and outside the type allow-list
// (empty list = keep all), then keep only edges whose endpoints both survived.
export function buildGraph(data: GraphData, { minDegree, types }: BuildGraphOptions): BuiltGraph {
  const nodes = data.nodes.filter(
    (n) => n.degree >= minDegree && (types.length === 0 || types.includes(n.type)),
  );
  const titles = new Set(nodes.map((n) => n.title));
  const edges = data.edges.filter((e) => titles.has(e.source) && titles.has(e.target));
  return { nodes, edges };
}

export type LegendEntry =
  | { kind: "community"; community: number; count: number }
  | { kind: "other"; count: number }
  | { kind: "none"; count: number };

// Legend for the nodes on screen: the `top` largest communities by node count
// (ties by community id), the rest folded into one "other" entry, then the
// nodes outside any community at this level.
export function communityLegend(nodes: GraphNode[], top = 8): LegendEntry[] {
  const counts = new Map<number, number>();
  let none = 0;
  for (const n of nodes) {
    if (n.community === null) none += 1;
    else counts.set(n.community, (counts.get(n.community) ?? 0) + 1);
  }
  const ranked = [...counts].sort((a, b) => b[1] - a[1] || a[0] - b[0]);
  const entries: LegendEntry[] = ranked.slice(0, top).map(([community, count]) => ({ kind: "community", community, count }));
  const rest = ranked.slice(top).reduce((sum, [, count]) => sum + count, 0);
  if (rest > 0) entries.push({ kind: "other", count: rest });
  if (none > 0) entries.push({ kind: "none", count: none });
  return entries;
}
