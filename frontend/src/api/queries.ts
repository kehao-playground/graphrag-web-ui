import { keepPreviousData, queryOptions } from "@tanstack/react-query";
import {
  api, apiJson, fetchArtifactDetail, fetchArtifacts, fetchGraph,
} from "./client";
import type { ArtifactListParams } from "./client";
import type {
  ArtifactTableName, AuditPage, BatchHealth, EnvOut, FilesOut, JobPage, Matrix, Member, Preflight,
  PreviewOut, Project, ProjectHealth, QuestionList, QuestionSetList, ResultList, Role,
  SettingsOut, SettingsVersionPage, TagCatalog, User, UserBrief,
} from "./types";

// Every server read the SPA makes, defined once (R1-17): key, path and
// queryFn live together, so two components reading the same resource can
// never drift apart on either. Components spread a factory into useQuery
// and add only view state (`enabled`, polling); mutations invalidate by
// `factory(...).queryKey`. Keys nest under their owner, so invalidating a
// prefix (e.g. projectJobsKey(pid)) also refreshes what hangs below it
// (every page of projectJobs and jobsPreflight).
//
// Freshness (R1-87): the listings that hash or scan the whole input tree
// (/health, /projects/health, /files) are fresh for 30 s and skip focus
// refetches; mutations invalidate them explicitly. Every other key keeps
// TanStack's refetch-on-mount default.

// One page of a paged listing (decision D1): the server answers
// {items, total} for these bounds.
export interface Page { limit: number; offset: number }
const pageParams = (p: Page) => new URLSearchParams({
  limit: String(p.limit), offset: String(p.offset),
}).toString();

const EXPENSIVE = { staleTime: 30_000, refetchOnWindowFocus: false } as const;
const QUIET = { meta: { silent: true } } as const;

// ---- projects, members, users, roles ------------------------------------

export const projectsList = () => queryOptions({
  queryKey: ["projects"],
  queryFn: () => apiJson<Project[]>("/api/projects", "projects.loadFailed"),
});

export const projectById = (pid: string) => queryOptions({
  queryKey: ["projects", pid],
  queryFn: () => apiJson<Project>(`/api/projects/${pid}`, "projects.loadFailed"),
});

export const projectMembers = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "members"],
  queryFn: () => apiJson<Member[]>(`/api/projects/${pid}/members`, "projectDetail.loadMembersFailed"),
});

// The narrow user list every logged-in user may read (owner names, member
// picker) — a different endpoint and shape from adminUsers().
export const usersBrief = () => queryOptions({
  queryKey: ["users"],
  queryFn: () => apiJson<UserBrief[]>("/api/users", "projects.loadUsersFailed"),
});

export const adminUsers = () => queryOptions({
  queryKey: ["admin", "users"],
  queryFn: () => apiJson<User[]>("/api/admin/users", "projects.loadUsersFailed"),
});

export const adminRoles = () => queryOptions({
  queryKey: ["admin", "roles"],
  queryFn: () => apiJson<Role[]>("/api/admin/roles", "adminRoles.loadFailed"),
});

// Grantable role catalog per scope; every logged-in user may read it.
export const roleCatalog = (scope: "global" | "project") => queryOptions({
  queryKey: ["roles", scope],
  queryFn: () => apiJson<Role[]>(
    `/api/roles?scope=${scope}`,
    scope === "global" ? "adminUsers.loadRolesFailed" : "projectDetail.loadRolesFailed",
  ),
});

export interface AuditFilter {
  limit: number;
  offset: number;
  action: string;
  targetType: string;
}

export const adminAudit = (f: AuditFilter) => queryOptions({
  queryKey: ["admin", "audit", f],
  queryFn: () => {
    const params = new URLSearchParams({ limit: String(f.limit), offset: String(f.offset) });
    if (f.action) params.set("action", f.action);
    if (f.targetType) params.set("target_type", f.targetType);
    return apiJson<AuditPage>(`/api/admin/audit?${params}`, "adminAudit.loadFailed");
  },
});

// ---- health --------------------------------------------------------------

// Sidebar badges and the overview page share this one request, so the two
// can never disagree on the counts. A failed fetch resolves to null (not
// an error): a missing badge costs nothing, and the overview's empty state
// stays deterministic whichever observer registers first.
export const projectHealth = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "health"],
  queryFn: async (): Promise<ProjectHealth | null> => {
    const r = await api(`/api/projects/${pid}/health`);
    return r.ok ? ((await r.json()) as ProjectHealth) : null;
  },
  ...EXPENSIVE,
  ...QUIET,
});

// One round trip for the whole visible project list (spec §7.5): the ids
// ride in the key so a changed list refetches; null on failure costs the
// column, not the page.
export const projectsHealth = (ids: string) => queryOptions({
  queryKey: ["projects", "health-batch", ids],
  queryFn: async (): Promise<BatchHealth | null> => {
    const r = await api(`/api/projects/health?ids=${ids}`);
    return r.ok ? ((await r.json()) as BatchHealth) : null;
  },
  enabled: ids.length > 0,
  ...EXPENSIVE,
  ...QUIET,
});

// ---- files -----------------------------------------------------------------

export const projectFiles = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "files"],
  queryFn: () => apiJson<FilesOut>(`/api/projects/${pid}/files`, "files.loadFailed"),
  ...EXPENSIVE,
});

// Quiet: a missing catalog costs the filter's options, not the panel.
export const projectTags = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "tags"],
  queryFn: () => apiJson<TagCatalog>(`/api/projects/${pid}/tags`, "files.loadTagsFailed"),
  ...QUIET,
});

// A locator pins WHERE in the document the preview window centers:
// {resultId, entryId} for a stored run, {passage} for an ad-hoc answer
// (spec §7.4); entryId is a number to match Citation.ids.
export type Locator = { resultId: string; entryId: number } | { passage: string };

// The drawer renders its own error, so no toast. The locator is part of
// the key: a new pin on the same document refetches.
export const filePreview = (pid: string, name: string, locator: Locator | null) => queryOptions({
  queryKey: ["projects", pid, "files", name, "preview", locator],
  queryFn: () => {
    const url = `/api/projects/${pid}/files/${encodeURIComponent(name)}/preview`;
    // No locator = the head window (GET); a locator always POSTs its
    // binding body — {result_id, entry_id} makes the server re-read the
    // stored passage, {passage} searches the document for cited text.
    return apiJson<PreviewOut>(url, "files.previewLoadFailed", locator ? {
      method: "POST",
      body: JSON.stringify("passage" in locator
        ? { passage: locator.passage }
        : { result_id: locator.resultId, entry_id: locator.entryId }),
    } : undefined);
  },
  ...QUIET,
});

// ---- jobs ----------------------------------------------------------------

// Everything job-shaped of a project hangs under this prefix: the list
// pages and the preflight.
export const projectJobsKey = (pid: string) => ["projects", pid, "jobs"] as const;

// The prefix of every page of the jobs list, without the preflight.
export const projectJobListKey = (pid: string) => [...projectJobsKey(pid), "list"] as const;

// One page of the jobs page's list: the launchable types only — test runs
// have the workbench (decision D2).
export const projectJobs = (pid: string, page: Page) => queryOptions({
  queryKey: [...projectJobListKey(pid), page],
  queryFn: () => apiJson<JobPage>(
    `/api/projects/${pid}/jobs?type=index&type=update&${pageParams(page)}`, "jobs.loadFailed",
  ),
  placeholderData: keepPreviousData,
});

// Shared by the documents, jobs and workbench panes: active_job is what
// freezes uploads and blocks a test run. Quiet (the jobs pane re-enables
// the toast): a missing preflight costs a notice, and a stale miss still
// surfaces as the backend's 409.
export const jobsPreflight = (pid: string) => queryOptions({
  queryKey: [...projectJobsKey(pid), "preflight"],
  queryFn: () => apiJson<Preflight>(`/api/projects/${pid}/jobs/preflight`, "jobs.loadPreflightFailed"),
  ...QUIET,
});

// ---- settings ------------------------------------------------------------

export const projectSettings = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "settings"],
  queryFn: () => apiJson<SettingsOut>(`/api/projects/${pid}/settings`, "settings.loadFailed"),
});

export const settingsVersionsKey = (pid: string) => ["projects", pid, "versions"] as const;

export const settingsVersions = (pid: string, page: Page) => queryOptions({
  queryKey: [...settingsVersionsKey(pid), page],
  queryFn: () => apiJson<SettingsVersionPage>(
    `/api/projects/${pid}/settings/versions?${pageParams(page)}`, "settings.loadVersionsFailed",
  ),
  placeholderData: keepPreviousData,
});

export const projectEnv = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "env"],
  queryFn: () => apiJson<EnvOut>(`/api/projects/${pid}/env`, "settings.loadEnvFailed"),
});

// ---- retrieval tests -----------------------------------------------------

export const questionSets = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "question-sets"],
  queryFn: () => apiJson<QuestionSetList>(`/api/projects/${pid}/question-sets`, "workbench.loadSetsFailed"),
});

export const setQuestions = (pid: string, setId: string) => queryOptions({
  queryKey: ["projects", pid, "question-sets", setId, "questions"],
  queryFn: () => apiJson<QuestionList>(
    `/api/projects/${pid}/question-sets/${setId}/questions`, "workbench.loadQuestionsFailed",
  ),
});

// The matrix window: the backend's default (5 most recent runs).
export const testRunMatrix = (pid: string) => queryOptions({
  queryKey: ["projects", pid, "test-runs"],
  queryFn: () => apiJson<Matrix>(`/api/projects/${pid}/test-runs`, "workbench.loadMatrixFailed"),
});

// One run's ordered results: the result drawer walks it, the run diff
// reads both sides from it, so a diff right after rating costs nothing.
export const runResults = (runId: string) => queryOptions({
  queryKey: ["test-runs", runId, "results"],
  queryFn: () => apiJson<ResultList>(`/api/test-runs/${runId}/results`, "workbench.loadResultsFailed"),
});

// ---- explore ---------------------------------------------------------------

export const artifactList = (pid: string, table: ArtifactTableName, params: ArtifactListParams) =>
  queryOptions({
    queryKey: ["projects", pid, "artifacts", table, params],
    queryFn: () => fetchArtifacts(pid, table, params),
    placeholderData: keepPreviousData,
  });

export const artifactDetail = (pid: string, table: ArtifactTableName, hrid: number) => queryOptions({
  queryKey: ["projects", pid, "artifacts", table, "detail", hrid],
  queryFn: () => fetchArtifactDetail(pid, table, hrid),
});

export const artifactGraph = (pid: string, level: number | undefined) => queryOptions({
  queryKey: ["projects", pid, "artifacts", "graph", level ?? null],
  queryFn: () => fetchGraph(pid, level),
  placeholderData: keepPreviousData,
});
