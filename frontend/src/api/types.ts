export interface User {
  id: string; email: string; display_name: string;
  role: "admin" | "user"; is_active: boolean; must_change_password: boolean;
}
// GET /api/users 的窄清單(所有已登入者可用;刻意無 role 等管理欄位)
export interface UserBrief {
  id: string; email: string; display_name: string; is_active: boolean;
}
export interface Project {
  id: string; name: string; slug: string; description: string | null;
  input_file_type: "text" | "csv" | "json"; owner_id: string; created_at: string;
}
export interface Member { user_id: string; email: string; display_name: string; role: string }
// GET /api/projects/{id}/files (Task 2): files are sorted by name.
export interface FileEntry { name: string; size: number; modified_at: string }
export interface FilesOut { files: FileEntry[]; usage_bytes: number; quota_bytes: number }
// Settings tab (Task 3/4/5 APIs): content + hash for optimistic locking
export interface SettingsOut { content: string; content_hash: string }
export interface SettingsVersionOut { id: number; content_hash: string; saved_by: string; created_at: string }
export interface SettingsVersionDetail extends SettingsVersionOut { content: string }
export interface EnvKeyOut { key: string; masked: string }
// Jobs tab (Task 7): mirror backend api/schemas.py JobOut/LastRunOut/PreflightOut.
export interface Job {
  id: string; project_id: string;
  type: "index" | "update"; method: "standard" | "fast";
  status: string; display_status: string;
  cancel_requested_at: string | null;
  exit_code: number | null; error: string | null;
  stats: Record<string, unknown> | null;
  queued_by: string; queued_at: string;
  started_at: string | null; finished_at: string | null;
  argv: string[];
}
export interface LastRun {
  type: string; status: string; finished_at: string | null;
  total_runtime_seconds: number | null; num_documents: number | null;
  update_documents: number | null;
}
export interface Preflight {
  active_job: Job | null; last_run: LastRun | null;
  cache_bytes: number; cache_quota_mb: number;
  disk_free_mb: number; disk_watermark_mb: number;
}
// display_status → antd Tag color; unknown statuses fall back to "default".
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
export type QueryMethod = "local" | "global" | "drift" | "basic";
export interface Citation {
  label: string; ids: number[];
  entries: { id: number; text: string | null }[];
}
export interface QueryTimings {
  frames_ms: number; search_ms: number; citations_ms: number; total_ms: number;
}
// Explore tab (Task 4): mirrors the GET /api/projects/{id}/artifacts/* JSON
// envelopes. Rows are projections of the parquet list_columns; the detail row
// is the full record. Graph* types are consumed by GraphView (Task 5).
export type ArtifactTableName =
  | "entities" | "relationships" | "communities"
  | "community_reports" | "text_units" | "documents";
export interface ArtifactPage {
  rows: Record<string, unknown>[];
  total: number;
  stale: boolean;
}
export interface ArtifactDetail {
  row: Record<string, unknown>;
  stale: boolean;
}
export interface GraphNode {
  hrid: number; title: string; type: string;
  degree: number; frequency: number; community: number | null;
}
export interface GraphEdge { source: string; target: string; weight: number }
export interface GraphData {
  level: number; levels: number[]; nodes: GraphNode[]; edges: GraphEdge[]; stale: boolean;
}
