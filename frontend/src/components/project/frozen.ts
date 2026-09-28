import type { Preflight } from "../../api/types";

// An index or update freezes the project's inputs (documents, settings,
// keys); a test run does not. Read off the shared preflight query, so a
// missed transition still surfaces as the backend's 409 project_indexing.
export function isFrozen(preflight: Preflight | undefined): boolean {
  const type = preflight?.active_job?.type;
  return type === "index" || type === "update";
}
