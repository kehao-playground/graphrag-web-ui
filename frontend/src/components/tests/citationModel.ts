import type { ArtifactTableName, Citation } from "../../api/types";

// The citations payload is one group per `[Data: …]` group of the answer,
// so a document cited five times arrives five times (R4-10). This module
// folds it into what the panel shows: Sources deduplicated by document,
// every other label deduplicated by id — and keeps the (key, id) pairs the
// answer's inline markers anchor to.

// Mirror of domain/citations.py _LABEL_KEYS: singular/plural fold.
const LABEL_KEYS: Record<string, string> = {
  sources: "sources", source: "sources",
  entities: "entities", entity: "entities",
  relations: "relationships", relation: "relationships",
  relationships: "relationships", relationship: "relationships",
  reports: "reports", report: "reports",
  communities: "communities", community: "communities",
  community_reports: "community_reports",
  text_units: "text_units", text_unit: "text_units",
  units: "units", unit: "units",
};

export function citationKey(label: string): string {
  const normalized = label.trim().toLowerCase().replace(/\s+/g, "_");
  return LABEL_KEYS[normalized] ?? normalized;
}

// The Explore table a cited id opens in. graphrag's context ids are the
// artifacts' human_readable_id (a report's is its community number), which
// is the key Explore's row detail reads.
export const EXPLORE_TABLE: Partial<Record<string, ArtifactTableName>> = {
  entities: "entities",
  relationships: "relationships",
  reports: "community_reports",
  community_reports: "community_reports",
  communities: "communities",
};

export interface Passage { id: number; text: string | null }
export interface SourceDoc { name: string | null; passages: Passage[] }
export interface CitedGroup { key: string; label: string; items: Passage[] }
export interface CitationModel {
  sources: SourceDoc[];
  groups: CitedGroup[];
  // Distinct cited items (passages + group items).
  count: number;
  has: (key: string, id: number) => boolean;
}

export function buildCitationModel(citations: Citation[]): CitationModel {
  const seen = new Set<string>();
  const docs = new Map<string, SourceDoc>();
  const groups = new Map<string, CitedGroup>();
  for (const c of citations) {
    const key = citationKey(c.label);
    const byId = new Map(c.entries.map((en) => [en.id, en]));
    for (const id of c.ids) {
      const pair = `${key}:${id}`;
      if (seen.has(pair)) continue;
      seen.add(pair);
      const en = byId.get(id);
      const item = { id, text: en?.text ?? null };
      if (key === "sources") {
        // An unnamed source cannot be grouped with anything: its own doc.
        const name = en?.source_name ?? null;
        const docKey = name ?? `\u0000${id}`;
        const doc = docs.get(docKey) ?? { name, passages: [] };
        doc.passages.push(item);
        docs.set(docKey, doc);
      } else {
        const g = groups.get(key) ?? { key, label: c.label, items: [] };
        g.items.push(item);
        groups.set(key, g);
      }
    }
  }
  return {
    sources: [...docs.values()],
    groups: [...groups.values()],
    count: seen.size,
    has: (key, id) => seen.has(`${key}:${id}`),
  };
}

// Same grammar as domain/citations.py GROUP_RE, plus graphrag's trailing
// "+more" (the backend skips such a group; here it only loses the +more).
const GROUP_RE = /^([A-Za-z ]+?)\s*\(([\d,\s]+?)(?:,\s*\+more)?\)$/;

export interface MarkerGroup { label: string; key: string; ids: number[] }

export function parseMarker(raw: string): MarkerGroup[] {
  const inner = raw.replace(/^\[Data:\s*/, "").replace(/\]$/, "");
  const out: MarkerGroup[] = [];
  for (const part of inner.split(";")) {
    const m = GROUP_RE.exec(part.trim());
    if (!m) continue;
    const ids = [...new Set((m[2].match(/\d+/g) ?? []).map(Number))];
    if (ids.length === 0) continue;
    const label = m[1].trim();
    out.push({ label, key: citationKey(label), ids });
  }
  return out;
}
