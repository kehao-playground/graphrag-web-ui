import { useState } from "react";
import { Outlet, useParams, useOutletContext } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Descriptions, Form, Input, Modal, Popconfirm, Select, Space, Spin, Table, Tag,
  Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { ApiRequestError, apiJson, sendOk } from "../api/client";
import { projectById, projectMembers, projectsList, roleCatalog, usersBrief } from "../api/queries";
import type { Member, Project } from "../api/types";
import Forbidden from "../components/Forbidden";
import { permLabel, roleLabel } from "../components/labels";
import FilesPanel from "../components/FilesPanel";
import SettingsPanel from "../components/SettingsPanel";
import JobsPanel from "../components/JobsPanel";
import Workbench from "../components/tests/Workbench";
import ExplorePanel from "../components/ExplorePanel";
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

// Atoms a role change must confirm before granting (R4-16): settings and
// API keys, and control of the project itself.
const SENSITIVE_ATOMS = ["project:edit_settings", "project:manage"];

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
          <Outlet context={ctx} />
        </div>
      </div>
    </div>
  );
}

export function ProjectPane({ pane }: { pane: PaneKey }) {
  const ctx = useOutletContext<ProjectPaneContext>();
  const { t } = useTranslation();
  const { projectId, project: p, canEditFiles, canRunJobs, canEditSettings } = ctx;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
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
        <Workbench projectId={projectId} canUse canRunJobs={canRunJobs} canEdit={canEditFiles} />
      )}
      {pane === "explore" && (
        // Same gating as Query: every member can browse the indexed artifacts.
        <ExplorePanel projectId={projectId} canUse />
      )}
      {pane === "settings" && <SettingsPanel projectId={projectId} canEdit={canEditSettings} />}
      {pane === "members" && <MembersPane ctx={ctx} />}
    </Space>
  );
}

// The project facts the old overview tab showed (spec §4 moves them to
// the members pane, where ProjectInfoDescriptions now lives). A manager
// edits the name and description from here (R3-08).
function ProjectInfoDescriptions({ p, owner, canManage }: {
  p: Project; owner: Member | undefined; canManage: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [editing, setEditing] = useState(false);
  return (
    <>
      <Descriptions
        title={t("projectDetail.infoTitle")}
        extra={canManage && (
          <Button size="small" onClick={() => setEditing(true)}>{t("projectDetail.editInfo")}</Button>
        )}
        bordered
        size="small"
        column={2}
        items={[
          { key: "name", label: t("common.name"), children: p.name },
          { key: "slug", label: t("projectDetail.slug"), children: p.slug },
          { key: "description", label: t("common.description"), children: p.description || t("common.notApplicable") },
          { key: "type", label: t("projects.inputFormat"), children: <Tag>{p.input_file_type}</Tag> },
          { key: "created", label: t("common.createdAt"), children: new Date(p.created_at).toLocaleString(i18n.language) },
          { key: "owner", label: t("projects.owner"), children: owner ? t("projectDetail.ownerWithNameEmail", { name: owner.display_name, email: owner.email }) : t("common.notApplicable") },
        ]}
      />
      {canManage && <EditProjectModal p={p} open={editing} onClose={() => setEditing(false)} />}
    </>
  );
}

interface ProjectInfoForm { name: string; description?: string }

function EditProjectModal({ p, open, onClose }: { p: Project; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form] = Form.useForm<ProjectInfoForm>();

  const save = useMutation({
    mutationFn: (v: ProjectInfoForm) => apiJson<Project>(
      `/api/projects/${p.id}`, "projectDetail.updateProjectFailed",
      { method: "PATCH", body: JSON.stringify({ name: v.name.trim(), description: v.description ?? "" }) },
    ),
    onSuccess: (updated) => {
      // The PATCH answers the full project (my_permissions included), so
      // the layout's heading updates without a refetch; the list refetches.
      qc.setQueryData(projectById(p.id).queryKey, updated);
      void qc.invalidateQueries({ queryKey: projectsList().queryKey, exact: true });
      message.success(t("projectDetail.projectUpdated"));
      onClose();
    },
  });

  return (
    <Modal
      title={t("projectDetail.editInfoTitle")}
      open={open}
      okText={t("common.save")}
      cancelText={t("common.cancel")}
      confirmLoading={save.isPending}
      onCancel={onClose}
      onOk={() => form.validateFields().then((v) => save.mutate(v))}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ name: p.name, description: p.description ?? "" }}
        preserve={false}
      >
        <Form.Item
          name="name"
          label={t("common.name")}
          rules={[
            { required: true, whitespace: true, message: t("projects.nameRequired") },
            { max: 200, message: t("projects.nameMax") },
          ]}
        >
          <Input />
        </Form.Item>
        <Form.Item name="description" label={t("common.description")}>
          <Input.TextArea rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  );
}

type PutMember = { userId: string; roleId: string; email?: string };

// Members and project info (spec §4). Its reads run only while the pane is
// mounted (R1-56): the users list and role catalog are for the add bar and
// the role selects, nothing else in the project needs them.
function MembersPane({ ctx }: { ctx: ProjectPaneContext }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [modal, modalHolder] = Modal.useModal();
  const { projectId: id, canManage } = ctx;
  const [addUserId, setAddUserId] = useState<string>();
  const [pickedRole, setPickedRole] = useState<string>();

  const members = useQuery(projectMembers(id));
  const rolesQ = useQuery(roleCatalog("project"));
  // Adding a member needs user_id; GET /api/users is the narrow list every
  // logged-in user can call, so any project:manage holder can pick users
  // (the frontend filters out disabled ones)
  const users = useQuery({ ...usersBrief(), enabled: canManage });
  const invalidateMembers = () => qc.invalidateQueries({ queryKey: projectMembers(id).queryKey });

  // owner is not grantable (single-owner policy; the owner row renders locked)
  const grantable = (rolesQ.data ?? []).filter((r) => r.name !== "owner");
  const roleOptions = grantable.map((r) => ({ label: roleLabel(r, t), value: r.id }));
  const roleById = new Map((rolesQ.data ?? []).map((r) => [r.id, r] as const));

  // The add-member role defaults to the least privileged built-in, viewer
  // (R4-16), until the manager picks one.
  const addRole = pickedRole
    ?? (grantable.find((r) => r.is_system && r.name === "viewer") ?? grantable[0])?.id;

  const putMember = useMutation({
    mutationFn: ({ userId, roleId }: PutMember) => sendOk(
      `/api/projects/${id}/members/${userId}`, "projectDetail.updateMemberFailed",
      { method: "PUT", body: JSON.stringify({ role_id: roleId }) },
    ),
    onSuccess: (_, v) => {
      if (v.email) {
        message.success(t("projectDetail.roleChanged", { email: v.email }));
      } else {
        message.success(t("projectDetail.memberAdded"));
        setAddUserId(undefined);
      }
      void invalidateMembers();
    },
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => sendOk(
      `/api/projects/${id}/members/${userId}`, "projectDetail.removeMemberFailed", { method: "DELETE" },
    ),
    onSuccess: () => {
      message.success(t("projectDetail.memberRemoved"));
      void invalidateMembers();
    },
  });

  // A change that grants settings/keys or project control asks first
  // (R4-16); any other change applies at once. Both end in a toast.
  const changeRole = (m: Member, roleId: string) => {
    const next = roleById.get(roleId);
    const had = new Set(roleById.get(m.role_id)?.permissions ?? []);
    const gained = (next?.permissions ?? []).filter((a) => SENSITIVE_ATOMS.includes(a) && !had.has(a));
    const apply = () => putMember.mutateAsync({ userId: m.user_id, roleId, email: m.email });
    if (!next || gained.length === 0) {
      void apply().catch(() => {});
      return;
    }
    modal.confirm({
      title: t("projectDetail.roleChangeTitle", { email: m.email, role: roleLabel(next, t) }),
      content: t("projectDetail.roleChangeBody", {
        perms: next.permissions.map((a) => permLabel(a, t)).join(t("common.listSeparator")),
      }),
      okText: t("projectDetail.roleChangeOk"),
      cancelText: t("common.cancel"),
      // A failed PUT toasts through the mutation cache; the dialog closes.
      onOk: () => apply().catch(() => {}),
    });
  };

  const rows = members.data ?? [];
  const owner = rows.find((m) => m.role_name === "owner");
  const memberIds = new Set(rows.map((m) => m.user_id));
  const addableUsers = (users.data ?? []).filter((u) => u.is_active && !memberIds.has(u.id));

  const columns: TableProps<Member>["columns"] = [
    { title: t("common.email"), dataIndex: "email" },
    { title: t("common.displayName"), dataIndex: "display_name" },
    {
      title: t("common.role"),
      dataIndex: "role_id",
      width: 140,
      render: (_, m) => (
        <Select
          size="small"
          style={{ width: 140 }}
          value={m.role_id}
          // owner is filtered out of the grantable catalog, but its locked
          // row still needs an option — otherwise the Select would render
          // the raw role uuid instead of a label
          options={m.role_name === "owner"
            ? [{ label: t("roles.owner"), value: m.role_id }]
            : roleOptions}
          // The owner row is 400-protected on the backend; the UI locks it rather than offer a guaranteed failure
          disabled={!canManage || m.role_name === "owner"}
          onChange={(roleId) => changeRole(m, roleId)}
        />
      ),
    },
    {
      title: t("common.actions"),
      width: 90,
      render: (_, m) =>
        canManage && m.role_name !== "owner" ? (
          <Popconfirm
            title={t("projectDetail.removeTitle", { email: m.email })}
            okText={t("projectDetail.remove")}
            okButtonProps={{ danger: true }}
            onConfirm={() => removeMember.mutate(m.user_id)}
          >
            <Button danger size="small">{t("projectDetail.remove")}</Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <>
      {modalHolder}
      <ProjectInfoDescriptions p={ctx.project} owner={owner} canManage={canManage} />
      {canManage && (
        <Space style={{ marginBottom: 16 }} title={t("projectDetail.addMember")}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder={t("projectDetail.selectUser")}
            style={{ minWidth: 240 }}
            value={addUserId}
            options={addableUsers.map((u) => ({ label: t("projectDetail.ownerWithNameEmail", { name: u.display_name, email: u.email }), value: u.id }))}
            onChange={setAddUserId}
            loading={users.isPending}
          />
          <Select
            style={{ width: 140 }}
            value={addRole}
            options={roleOptions}
            onChange={setPickedRole}
          />
          <Button
            type="primary"
            disabled={!addUserId || !addRole}
            loading={putMember.isPending && !putMember.variables?.email}
            onClick={() => {
              if (addUserId && addRole) putMember.mutate({ userId: addUserId, roleId: addRole });
            }}
          >
            {t("projectDetail.add")}
          </Button>
        </Space>
      )}
      <Table
        rowKey="user_id"
        size="small"
        loading={members.isFetching}
        dataSource={rows}
        columns={columns}
        pagination={false}
      />
    </>
  );
}
