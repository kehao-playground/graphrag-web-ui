import { useTranslation } from "react-i18next";

// Human-readable binary units: sub-KiB sizes stay in bytes; above that,
// one decimal below 100 and rounding above (a 5 GB project quota renders
// "4.9 GiB", not "5120000.0 KiB"). Lives in this shared non-component module
// (like JobStatusColor in api/types.ts) because fast-refresh requires
// component files to export components only.
export const humanBytes = (bytes: number): string => {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${value < 100 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
};

// Per-file index state (spec §6.3, FileEntryOut.index_state): the closed set
// the backend emits and the UI renders.
export type IndexState = "new" | "modified" | "removed" | "indexed" | "skipped";

export const INDEX_STATES: IndexState[] = ["new", "modified", "removed", "indexed", "skipped"];

// Each state carries a SENTENCE, not just a colored dot: `removed` and
// `skipped` are the two the user has never seen before, and both must
// explain themselves. `removed` specifically must say that only a full
// rebuild clears it - an update leaves the document in the index (spec 9.1).
export const STATE_COLOR: Record<IndexState, string> = {
  indexed: "green", new: "blue", modified: "gold",
  removed: "red", skipped: "volcano",
};

// The generated schema types index_state as a plain string; only the closed
// set above has catalog copy (spec §9.1: no component builds copy from the
// schema value), so callers guard with this before they look copy up.
export const isIndexState = (v: string): v is IndexState =>
  Object.hasOwn(STATE_COLOR, v);

// Labels and sentences per state, straight from the i18n catalog.
export function useStateCopy(): Record<IndexState, { label: string; sentence: string }> {
  const { t } = useTranslation();
  return {
    new: { label: t("files.state.new"), sentence: t("files.stateSentence.new") },
    modified: { label: t("files.state.modified"), sentence: t("files.stateSentence.modified") },
    removed: { label: t("files.state.removed"), sentence: t("files.stateSentence.removed") },
    indexed: { label: t("files.state.indexed"), sentence: t("files.stateSentence.indexed") },
    skipped: { label: t("files.state.skipped"), sentence: t("files.stateSentence.skipped") },
  };
}
