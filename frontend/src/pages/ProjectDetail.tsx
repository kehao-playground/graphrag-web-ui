import { Suspense } from "react";
import { Outlet, useParams, useOutletContext } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Space, Spin, Typography } from "antd";
import { ApiRequestError } from "../api/client";
import { projectById } from "../api/queries";
import type { Project } from "../api/types";
import Forbidden from "../components/Forbidden";
import FilesPanel from "../components/FilesPanel";
import SettingsPanel from "../components/SettingsPanel";
import JobsPanel from "../components/JobsPanel";
import Workbench from "../components/tests/Workbench";
import ExplorePanel from "../components/ExplorePanel";
import MembersPane from "../components/project/MembersPane";
import ProjectSidebar from "../components/project/ProjectSidebar";
import { useActiveJobWatch } from "../components/project/useActiveJobWatch";
import ProjectOverview from "./ProjectOverview";

// The routed panes (spec §4). App nests one <ProjectPane pane=…> per route
// under /projects/:id; every pane reads this layout through the outlet
// context, so the project query and permission atoms live here exactly once.
export type PaneKey = "overview" | "files" | "jobs" | "tests" | "explore" | "settings" | "members";

// What the layout hands each pane. Pane-specific reads (the members pane's
// members, users and role catalog) live in the pane itself, so they run
// only while it is mounted (R1-56).
export interface ProjectPaneContext {
  projectId: string;
  project: Project;
  canManage: boolean;
  canEditFiles: boolean;
  canRunJobs: boolean;
  canEditSettings: boolean;
}

// One heading per pane, so a deep link lands legible. The workbench pane
// gets the fuller title the plan pins; the rest reuse their entry labels.
// The overview pane is the exception: ProjectOverview brings its own
// heading (the health heading), so it has no generic one here.
const PANE_HEADING = {
  files: "projectDetail.filesTab",
  jobs: "projectDetail.jobsTab",
  tests: "projectDetail.testsHeading",
  explore: "projectDetail.exploreTab",
  settings: "projectDetail.settingsTab",
  members: "projectDetail.membersTab",
} as const satisfies Record<Exclude<PaneKey, "overview">, string>;

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const { t } = useTranslation();

  const project = useQuery({ ...projectById(id ?? ""), enabled: !!id });
  // Mounted with the layout, so it watches the job slot whichever pane is
  // open; gated on the project so a 404/403 project never polls.
  useActiveJobWatch(id ?? "", project.isSuccess);

  if (!id) return <Alert type="warning" showIcon message={t("projectDetail.missingId")} />;
  if (project.isPending) return <Spin style={{ display: "block", marginTop: 64 }} />;
  if (project.error || !project.data) {
    // Refused or unknown: the no-access page, with its way back (R4-37).
    const status = project.error instanceof ApiRequestError ? project.error.status : 0;
    if (status === 403 || status === 404) return <Forbidden notFound={status === 404} />;
    return (
      <Alert
        type="error"
        showIcon
        message={t("projectDetail.loadProjectFailed")}
        description={project.error?.message ?? t("projectDetail.loadProjectDenied")}
        style={{ marginTop: 16 }}
      />
    );
  }

  const p = project.data;
  // Backend-computed permission atoms (spec §8): my_permissions already
  // folds in owner, ops act_any and custom project:manage roles, so the UI
  // never rebuilds a role→permission table.
  const myPerms = new Set(p.my_permissions ?? []);
  const ctx: ProjectPaneContext = {
    projectId: id,
    project: p,
    canManage: myPerms.has("project:manage"),
    canEditFiles: myPerms.has("project:edit_content"),
    canRunJobs: myPerms.has("project:run_jobs"),
    canEditSettings: myPerms.has("project:edit_settings"),
  };

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>{p.name}</Typography.Title>
      {/* Second-level nav inside the content area (spec §4): the global
          sider owns cross-project nav; this sidebar scopes to one project
          and the panes render beside it. */}
      <div style={{ display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
        <ProjectSidebar projectId={id} permissions={myPerms} />
        <div style={{ flex: 1, minWidth: 0 }}>
          {/* The pane is a lazy route element too: suspend here, not in
              Layout, so the heading and sidebar stay up meanwhile. */}
          <Suspense fallback={<Spin style={{ display: "block", marginTop: 64 }} />}>
            <Outlet context={ctx} />
          </Suspense>
        </div>
      </div>
    </div>
  );
}

export function ProjectPane({ pane }: { pane: PaneKey }) {
  const ctx = useOutletContext<ProjectPaneContext>();
  const { t } = useTranslation();
  const { projectId, project: p, canManage, canEditFiles, canRunJobs, canEditSettings } = ctx;

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {pane !== "overview" && (
        <Typography.Title level={4} style={{ margin: 0 }}>{t(PANE_HEADING[pane])}</Typography.Title>
      )}

      {pane === "overview" && (
        // The /health overview (Task 6): action card, stat tiles and
        // recent activity, sharing the sidebar's health query. It brings
        // its own heading, which is why it is absent from PANE_HEADING.
        <ProjectOverview projectId={projectId} />
      )}
      {pane === "files" && (
        <FilesPanel projectId={projectId} inputFileType={p.input_file_type} canEdit={canEditFiles} />
      )}
      {pane === "jobs" && <JobsPanel projectId={projectId} canEdit={canRunJobs} />}
      {pane === "tests" && (
        // The workbench hosts both the batch matrix and the ad-hoc stream;
        // the pane is visible to every member (viewer+ can read the matrix
        // and use the stream; launching a run needs project:run_jobs,
        // curating sets and questions project:edit_content).
        <Workbench projectId={projectId} canRunJobs={canRunJobs} canEdit={canEditFiles} />
      )}
      {pane === "explore" && (
        // Same gating as Query: every member can browse the indexed artifacts.
        <ExplorePanel projectId={projectId} />
      )}
      {pane === "settings" && <SettingsPanel projectId={projectId} canEdit={canEditSettings} />}
      {pane === "members" && <MembersPane project={p} canManage={canManage} />}
    </Space>
  );
}
