import type { ArtifactTables } from "../api/types";

// GET /api/artifact-tables as the backend registry answers it
// (domain/artifacts.py TABLES); test_explore_api pins the server side.
export const ARTIFACT_TABLES: ArtifactTables = {
  tables: [
    { name: "entities", columns: ["human_readable_id", "title", "type", "frequency", "degree"],
      type_filter: true, community_filter: true },
    { name: "relationships", columns: ["human_readable_id", "source", "target", "weight", "combined_degree"],
      type_filter: false, community_filter: false },
    { name: "communities", columns: ["human_readable_id", "community", "level", "parent", "size", "title"],
      type_filter: false, community_filter: true },
    { name: "community_reports", columns: ["human_readable_id", "community", "level", "rank", "title"],
      type_filter: false, community_filter: true },
    { name: "text_units", columns: ["human_readable_id", "n_tokens", "document_id"],
      type_filter: false, community_filter: false },
    { name: "documents", columns: ["human_readable_id", "title", "creation_date"],
      type_filter: false, community_filter: false },
  ],
};
