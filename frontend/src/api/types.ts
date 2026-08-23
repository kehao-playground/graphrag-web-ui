// API type layer (spec A5.2): every backend-pydantic-backed shape is an alias
// into types.generated.ts (openapi-typescript from the committed openapi.json —
// CI regenerates and diffs it). Components keep importing THESE names, so a
// backend model rename only touches this file. The remaining hand-written
// types have no backend response_model yet and are tagged accordingly.
import type { components } from "./types.generated";

export type User = components["schemas"]["UserOut"];
export type UserBrief = components["schemas"]["UserBriefOut"];
export type Project = components["schemas"]["ProjectOut"];
export type Member = components["schemas"]["MemberOut"];
export type FileEntry = components["schemas"]["FileEntryOut"];
export type FilesOut = components["schemas"]["FileListOut"];
export type SettingsOut = components["schemas"]["SettingsOut"];
export type SettingsVersionOut = components["schemas"]["VersionOut"];
export type SettingsVersionDetail = components["schemas"]["VersionDetailOut"];
export type EnvKeyOut = components["schemas"]["EnvKeyOut"];
export type Job = components["schemas"]["JobOut"];
export type LastRun = components["schemas"]["LastRunOut"];
export type Preflight = components["schemas"]["PreflightOut"];

// display_status → antd Tag color; unknown statuses fall back to "default".
// no backend response_model yet — hand-maintained (spec A5.2)
export const JobStatusColor: Record<string, string> = {
  queued: "blue",
  running: "gold",
  cancelling: "orange",
  succeeded: "green",
  failed: "red",
  "failed(interrupted)": "volcano",
  cancelled: "default",
};
// Query tab (Task 5): mirrors the SSE stream contract of
// GET /api/projects/{id}/query/stream — citations events carry [Citation],
// done events carry QueryTimings.
// no backend response_model yet — hand-maintained (spec A5.2)
export type QueryMethod = "local" | "global" | "drift" | "basic";
// no backend response_model yet — hand-maintained (spec A5.2)
export interface Citation {
  label: string; ids: number[];
  entries: { id: number; text: string | null }[];
}
// no backend response_model yet — hand-maintained (spec A5.2)
export interface QueryTimings {
  frames_ms: number; search_ms: number; citations_ms: number; total_ms: number;
}
// Explore tab (Task 4): mirrors the GET /api/projects/{id}/artifacts/* JSON
// envelopes. Rows are projections of the parquet list_columns; the detail row
// is the full record. Graph* types are consumed by GraphView (Task 5).
// no backend response_model yet — hand-maintained (spec A5.2)
export type ArtifactTableName =
  | "entities" | "relationships" | "communities"
  | "community_reports" | "text_units" | "documents";
// no backend response_model yet — hand-maintained (spec A5.2)
export interface ArtifactPage {
  rows: Record<string, unknown>[];
  total: number;
  stale: boolean;
}
// no backend response_model yet — hand-maintained (spec A5.2)
export interface ArtifactDetail {
  row: Record<string, unknown>;
  stale: boolean;
}
// no backend response_model yet — hand-maintained (spec A5.2)
export interface GraphNode {
  hrid: number; title: string; type: string;
  degree: number; frequency: number; community: number | null;
}
// no backend response_model yet — hand-maintained (spec A5.2)
export interface GraphEdge { source: string; target: string; weight: number }
// no backend response_model yet — hand-maintained (spec A5.2)
export interface GraphData {
  level: number; levels: number[]; nodes: GraphNode[]; edges: GraphEdge[]; stale: boolean;
}
