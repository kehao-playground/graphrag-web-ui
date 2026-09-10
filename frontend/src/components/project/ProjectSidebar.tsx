import { useQuery } from "@tanstack/react-query";
import { Badge, Menu } from "antd";
import type { MenuProps } from "antd";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import type { ProjectHealth } from "../../api/types";

// A sidebar entry = a pane route + the backend-computed atom that gates it
// (spec §8). An atom the viewer lacks removes the entry outright — hidden,
// never disabled; the pane itself stays URL-reachable and degrades there.
const ENTRIES = [
  { pane: "overview", labelKey: "projectDetail.overviewTab", requires: "project:view" },
  { pane: "files", labelKey: "projectDetail.filesTab", requires: "project:view" },
  { pane: "jobs", labelKey: "projectDetail.jobsTab", requires: "project:view" },
  { pane: "tests", labelKey: "projectDetail.testsTab", requires: "project:view" },
  { pane: "explore", labelKey: "projectDetail.exploreTab", requires: "project:view" },
  { pane: "settings", labelKey: "projectDetail.settingsTab", requires: "project:edit_settings" },
  { pane: "members", labelKey: "projectDetail.membersTab", requires: "project:manage" },
] as const;

// The three groups of spec §4: knowledge base, retrieval, manage. A group
// whose every entry is filtered out disappears rather than orphaning its label.
const GROUPS = [
  { labelKey: "projectDetail.groupKnowledgeBase", panes: ["overview", "files", "jobs"] },
  { labelKey: "projectDetail.groupRetrieval", panes: ["tests", "explore"] },
  { labelKey: "projectDetail.groupManage", panes: ["settings", "members"] },
] as const;

// Second-level nav for one project (spec §4): routed entries so every pane
// is reload-stable and linkable, badges fed by the /health aggregate so
// the sidebar and the overview page can never disagree on the counts.
export default function ProjectSidebar({ projectId, permissions }: {
  projectId: string;
  permissions: Set<string>;
}) {
  const { t } = useTranslation();
  const location = useLocation();

  // Badge source is the backend's health aggregate, not a client-side
  // count. Quiet on failure: a missing badge costs nothing, and the panes
  // own surfacing errors. The overview page shares this query key.
  const health = useQuery({
    queryKey: ["projects", projectId, "health"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/health`);
      return r.ok ? ((await r.json()) as ProjectHealth) : null;
    },
    retry: false,
  });

  // files → "not yet indexed" = new + modified (spec §9.1); jobs → one
  // running job is the only live state a job pane entry can carry.
  const badgeFor = (pane: string): number | undefined => {
    if (!health.data) return undefined;
    if (pane === "files") {
      const n = health.data.files.new + health.data.files.modified;
      return n > 0 ? n : undefined;
    }
    return pane === "jobs" && health.data.active_job ? 1 : undefined;
  };

  const base = `/projects/${projectId}/`;
  const items: MenuProps["items"] = GROUPS.map((g) => ({
    key: g.labelKey,
    type: "group" as const,
    label: t(g.labelKey),
    children: g.panes
      .map((pane) => ENTRIES.find((e) => e.pane === pane))
      .filter((e): e is (typeof ENTRIES)[number] => !!e && permissions.has(e.requires))
      .map((e) => {
        const badge = badgeFor(e.pane);
        return {
          key: e.pane,
          label: (
            // A real anchor (not a click handler) so entries are links:
            // shareable, middle-clickable, and reachable without JS routing.
            <Link to={`${base}${e.pane}`} style={{ display: "block", color: "inherit" }}>
              {t(e.labelKey)}
              {badge !== undefined && <Badge count={badge} style={{ marginLeft: 8 }} />}
            </Link>
          ),
        };
      }),
  })).filter((g) => (g.children?.length ?? 0) > 0);

  // Panes are flat routes; anything else (the bare /projects/:id index
  // before its redirect) highlights the landing pane.
  const selected = ENTRIES.find((e) => location.pathname === `${base}${e.pane}`)?.pane
    ?? "overview";

  return <Menu mode="inline" style={{ width: 200 }} items={items} selectedKeys={[selected]} />;
}
