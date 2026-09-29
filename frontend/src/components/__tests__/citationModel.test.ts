import { buildCitationModel, citationKey, parseMarker } from "../tests/citationModel";
import type { Citation } from "../../api/types";

test("labels fold singular/plural and spacing like the backend parser", () => {
  expect(citationKey("Sources")).toBe("sources");
  expect(citationKey("Entity")).toBe("entities");
  expect(citationKey("Relations")).toBe("relationships");
  expect(citationKey("Reports")).toBe("reports");
  expect(citationKey("Text Units")).toBe("text_units");
});

test("sources dedupe by document across groups, one passage per entry id", () => {
  const citations: Citation[] = [
    { label: "Sources", ids: [3], entries: [{ id: 3, text: "alpha", source_name: "a.txt" }] },
    { label: "Entities", ids: [55], entries: [{ id: 55, text: "ACME", source_name: null }] },
    { label: "Sources", ids: [3, 4], entries: [
      { id: 3, text: "alpha", source_name: "a.txt" },
      { id: 4, text: "beta", source_name: "a.txt" },
    ] },
    { label: "Sources", ids: [9], entries: [{ id: 9, text: "gamma", source_name: "b.txt" }] },
    { label: "Entity", ids: [55, 27], entries: [
      { id: 55, text: "ACME", source_name: null },
      { id: 27, text: "BOB", source_name: null },
    ] },
  ];
  const m = buildCitationModel(citations);
  expect(m.sources).toEqual([
    { name: "a.txt", passages: [{ id: 3, text: "alpha" }, { id: 4, text: "beta" }] },
    { name: "b.txt", passages: [{ id: 9, text: "gamma" }] },
  ]);
  expect(m.groups).toEqual([
    { key: "entities", label: "Entities", items: [{ id: 55, text: "ACME" }, { id: 27, text: "BOB" }] },
  ]);
  expect(m.count).toBe(5);
  expect(m.has("sources", 4)).toBe(true);
  expect(m.has("entities", 27)).toBe(true);
  expect(m.has("reports", 1)).toBe(false);
});

test("unnamed sources and ids without an entry stay separate", () => {
  const m = buildCitationModel([
    { label: "Sources", ids: [1, 2], entries: [{ id: 1, text: "x", source_name: null }] },
  ]);
  expect(m.sources).toEqual([
    { name: null, passages: [{ id: 1, text: "x" }] },
    { name: null, passages: [{ id: 2, text: null }] },
  ]);
});

test("a marker parses into keyed groups; +more and junk are dropped", () => {
  expect(parseMarker("[Data: Sources (3); Entities (55, 27, +more)]")).toEqual([
    { label: "Sources", key: "sources", ids: [3] },
    { label: "Entities", key: "entities", ids: [55, 27] },
  ]);
  expect(parseMarker("[Data: nonsense]")).toEqual([]);
});
