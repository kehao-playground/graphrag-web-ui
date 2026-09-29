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
export type Role = components["schemas"]["RoleOut"];
export type FileEntry = components["schemas"]["FileEntryOut"];
export type FilesOut = components["schemas"]["FileListOut"];
export type UploadedFile = components["schemas"]["FileOut"];
export type TagEntry = components["schemas"]["TagOut"];
export type TagCatalog = components["schemas"]["TagCatalogOut"];
export type BulkDeleteResult = components["schemas"]["BulkDeleteOut"];
export type SettingsOut = components["schemas"]["SettingsOut"];
export type SettingsVersionOut = components["schemas"]["VersionOut"];
export type SettingsVersionPage = components["schemas"]["VersionPageOut"];
export type SettingsVersionDetail = components["schemas"]["VersionDetailOut"];
export type EnvKeyOut = components["schemas"]["EnvKeyOut"];
export type Job = components["schemas"]["JobOut"];
export type JobPage = components["schemas"]["JobPageOut"];
export type LastRun = components["schemas"]["LastRunOut"];
export type Preflight = components["schemas"]["PreflightOut"];
export type ProjectHealth = components["schemas"]["HealthOut"];
export type BatchHealth = components["schemas"]["BatchHealthOut"];
export type QuestionSet = components["schemas"]["SetOut"];
export type Question = components["schemas"]["QuestionOut"];
export type TestRun = components["schemas"]["RunOut"];
export type MatrixCell = components["schemas"]["CellOut"];
export type MatrixRow = components["schemas"]["RowOut"];
export type TestResult = components["schemas"]["ResultOut"];
export type PreviewOut = components["schemas"]["PreviewOut"];
export type EnvOut = components["schemas"]["EnvOut"];
export type DryRunOut = components["schemas"]["DryRunOut"];
export type SettingsWriteOut = components["schemas"]["SettingsWriteOut"];
export type QuestionSetList = components["schemas"]["SetListOut"];
export type QuestionList = components["schemas"]["QuestionListOut"];
export type Matrix = components["schemas"]["MatrixOut"];
export type ResultList = components["schemas"]["ResultListOut"];
export type AuditEntry = components["schemas"]["AuditEntryOut"];
export type AuditPage = components["schemas"]["AuditPageOut"];
export type JobProgress = components["schemas"]["JobProgressOut"];

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
// Query tab: the POST /query body, whose citations/timings are also the
// SSE stream's `citations` and `done` event payloads (the stream route
// itself has no response_model — its frames reuse these shapes).
export type QueryMethod = components["schemas"]["QueryIn"]["method"];
export type Citation = components["schemas"]["CitationOut"];
export type QueryTimings = components["schemas"]["QueryTimingsOut"];
// Explore tab: GET /api/projects/{id}/artifacts/*.
// The table name is a path parameter, so it has no schema to alias.
export type ArtifactTableName =
  | "entities" | "relationships" | "communities"
  | "community_reports" | "text_units" | "documents";
export type ArtifactPage = components["schemas"]["ArtifactPageOut"];
export type ArtifactDetail = components["schemas"]["ArtifactDetailOut"];
export type GraphNode = components["schemas"]["GraphNodeOut"];
export type GraphEdge = components["schemas"]["GraphEdgeOut"];
export type GraphData = components["schemas"]["GraphOut"];

// Error envelopes (i18n spec §4.1), documented on every operation (R3-32):
// ApiErrorBody for a 4xx, ValidationErrorBody for a 422, SettingsConflict
// for the settings PUT 409.
export type ApiErrorBody = components["schemas"]["ApiErrorOut"];
export type ValidationErrorBody = components["schemas"]["ValidationErrorOut"];
export type SettingsConflict = components["schemas"]["SettingsConflictOut"];
