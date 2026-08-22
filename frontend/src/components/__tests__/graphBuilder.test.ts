import { test, expect } from "vitest";
import { buildGraph } from "../graphBuilder";
import type { GraphData } from "../../api/types";

const DATA: GraphData = {
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

test("minDegree filters nodes, then edges to surviving endpoints", () => {
  const { nodes, edges } = buildGraph(DATA, { minDegree: 2, types: [] });
  expect(nodes.map((n) => n.title)).toEqual(["Alan Turing"]);
  // both edges lost an endpoint (Ada Lovelace and Analytical Engine dropped)
  expect(edges).toEqual([]);
});

test("types filter keeps listed types only; empty types keeps everything", () => {
  const only = buildGraph(DATA, { minDegree: 0, types: ["PERSON"] });
  expect(only.nodes).toHaveLength(2);
  // Alan–Ada survives (both PERSON); Ada–Engine loses its ARTIFACT endpoint
  expect(only.edges.map((e) => [e.source, e.target])).toEqual([["Alan Turing", "Ada Lovelace"]]);

  const all = buildGraph(DATA, { minDegree: 0, types: [] });
  expect(all.nodes).toHaveLength(3);
  expect(all.edges).toHaveLength(2);
});

test("filters matching nothing return an empty result safely", () => {
  expect(buildGraph(DATA, { minDegree: 10, types: [] })).toEqual({ nodes: [], edges: [] });
  expect(buildGraph(DATA, { minDegree: 0, types: ["PLACE"] })).toEqual({ nodes: [], edges: [] });
});
