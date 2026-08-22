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
