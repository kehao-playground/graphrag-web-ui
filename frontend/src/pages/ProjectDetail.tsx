import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Descriptions, Popconfirm, Select, Space, Spin, Table, Tabs, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { Member, Project, Role, UserBrief } from "../api/types";
import FilesPanel from "../components/FilesPanel";
import SettingsPanel from "../components/SettingsPanel";
import JobsPanel from "../components/JobsPanel";
import QueryPanel from "../components/QueryPanel";
import ExplorePanel from "../components/ExplorePanel";

// Built-in role names are the backend seed's closed set, so the template
// key stays inside typed-t's key union; custom roles render their raw name.
type BuiltinRoleName =
  "user_admin" | "ops" | "viewer" | "maintainer" | "editor" | "owner";


export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  const [addUserId, setAddUserId] = useState<string>();
  const [addRole, setAddRole] = useState<string>();


  const project = useQuery({
    queryKey: ["projects", id],
    queryFn: async () => {
      const r = await api(`/api/projects/${id}`);
      if (!r.ok) throw new Error(await detailOf(r, "projects.loadFailed"));
      return (await r.json()) as Project;
    },
    enabled: !!id,
    retry: false,
  });

  const members = useQuery({
    queryKey: ["projects", id, "members"],
    queryFn: async () => {
      const r = await api(`/api/projects/${id}/members`);
      if (!r.ok) throw new Error(await detailOf(r, "projectDetail.loadMembersFailed"));
      return (await r.json()) as Member[];
    },
    enabled: !!id,
    retry: false,
  });

  useEffect(() => {
    if (project.error) message.error(project.error.message);
  }, [project.error]);
  useEffect(() => {
    if (members.error) message.error(members.error.message);
  }, [members.error]);

  // Backend-computed permission atoms (spec §8): my_permissions already
  // folds in owner, ops act_any and custom project:manage roles, so the UI
  // never rebuilds a role→permission table. Pending query → empty set.
  // Computed BEFORE the users query: its `enabled` reads canManage, and
  // every hook must stay above the early returns further down.
  const myPerms = new Set(project.data?.my_permissions ?? []);
  const canManage = myPerms.has("project:manage");
  const canEditFiles = myPerms.has("project:edit_content");
  const canRunJobs = myPerms.has("project:run_jobs");
  const canEditSettings = myPerms.has("project:edit_settings");

  // Member role catalog (GET /api/roles?scope=project) — a hook like the
  // queries above, so it too lives above the early returns.
  const rolesQ = useQuery({
    queryKey: ["roles", "project"],
    queryFn: async () => {
      const r = await api("/api/roles?scope=project");
      if (!r.ok) throw new Error(await detailOf(r, "projectDetail.loadRolesFailed"));
      return (await r.json()) as Role[];
    },
    retry: false,
  });

  useEffect(() => {
    if (rolesQ.error) message.error(rolesQ.error.message);
  }, [rolesQ.error]);

  const roleLabel = (r: Role) => (r.is_system ? t(`roles.${r.name as BuiltinRoleName}`) : r.name);
  // owner is not grantable (single-owner policy; the owner row renders locked)
  const MEMBER_ROLE_OPTIONS = (rolesQ.data ?? [])
    .filter((r) => r.name !== "owner")
    .map((r) => ({ label: roleLabel(r), value: r.id }));

  // Default the add-member role to the catalog's first grantable option
  // once it loads; later catalog refreshes keep the current choice.
  useEffect(() => {
    setAddRole((cur) => cur ?? MEMBER_ROLE_OPTIONS[0]?.value);
  }, [MEMBER_ROLE_OPTIONS[0]?.value]);

  // Adding a member needs user_id; GET /api/users is the narrow list every
  // logged-in user can call, so any project:manage holder can pick users
  // (the frontend filters out disabled ones)
  const users = useQuery({
    queryKey: ["users"],
    queryFn: async () => {
      const r = await api("/api/users");
      if (!r.ok) throw new Error(await detailOf(r, "projects.loadUsersFailed"));
      return (await r.json()) as UserBrief[];
    },
    enabled: canManage,
    retry: false,
  });

  useEffect(() => {
    if (users.error) message.error(users.error.message);
  }, [users.error]);

  const putMember = useMutation({
    mutationFn: async ({ userId, roleId }: { userId: string; roleId: string }) => {
      const r = await api(`/api/projects/${id}/members/${userId}`, {
        method: "PUT",
        body: JSON.stringify({ role_id: roleId }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "projectDetail.updateMemberFailed"));
    },
    onSuccess: () => {
      setAddUserId(undefined);
      qc.invalidateQueries({ queryKey: ["projects", id, "members"] });
    },
    onError: (e) => message.error(e.message),
  });

  const removeMember = useMutation({
    mutationFn: async (userId: string) => {
      const r = await api(`/api/projects/${id}/members/${userId}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, "projectDetail.removeMemberFailed"));
    },
    onSuccess: () => {
      message.success(t("projectDetail.memberRemoved"));
      qc.invalidateQueries({ queryKey: ["projects", id, "members"] });
    },
    onError: (e) => message.error(e.message),
  });

  if (!id) return <Alert type="warning" showIcon message={t("projectDetail.missingId")} />;
  if (project.isPending || members.isPending) return <Spin style={{ display: "block", marginTop: 64 }} />;
  if (project.error || !project.data) {
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
  const owner = members.data?.find((m) => m.role_name === "owner");
  const memberIds = new Set(members.data?.map((m) => m.user_id));

  const memberColumns: TableProps<Member>["columns"] = [
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
            : MEMBER_ROLE_OPTIONS}
          // The owner row is 400-protected on the backend; the UI locks it rather than offer a guaranteed failure
          disabled={!canManage || m.role_name === "owner"}
          onChange={(roleId) => putMember.mutate({ userId: m.user_id, roleId })}
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

  const addableUsers = (users.data ?? []).filter((u) => u.is_active && !memberIds.has(u.id));

  const items = [
    {
      key: "overview",
      label: t("projectDetail.overviewTab"),
      children: (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Descriptions
            title={t("projectDetail.infoTitle")}
            bordered
            size="small"
            column={2}
            items={[
              { key: "name", label: t("common.name"), children: p.name },
              { key: "slug", label: t("projectDetail.slug"), children: p.slug },
              { key: "description", label: t("common.description"), children: p.description ?? t("common.notApplicable") },
              { key: "type", label: t("projects.inputFormat"), children: <Tag>{p.input_file_type}</Tag> },
              { key: "created", label: t("common.createdAt"), children: new Date(p.created_at).toLocaleString(i18n.language) },
              { key: "owner", label: t("projects.owner"), children: owner ? t("projectDetail.ownerWithNameEmail", { name: owner.display_name, email: owner.email }) : t("common.notApplicable") },
            ]}
          />
          <Card title={t("projectDetail.membersTitle")} size="small">
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
                  options={MEMBER_ROLE_OPTIONS}
                  onChange={setAddRole}
                />
                <Button
                  type="primary"
                  disabled={!addUserId || !addRole}
                  loading={putMember.isPending}
                  onClick={() => addUserId && addRole && putMember.mutate({ userId: addUserId, roleId: addRole })}
                >
                  {t("projectDetail.add")}
                </Button>
              </Space>
            )}
            <Table
              rowKey="user_id"
              size="small"
              loading={members.isFetching}
              dataSource={members.data ?? []}
              columns={memberColumns}
              pagination={false}
            />
          </Card>
        </Space>
      ),
    },
    {
      key: "settings",
      label: t("projectDetail.settingsTab"),
      children: <SettingsPanel projectId={id} canEdit={canEditSettings} />,
    },
    {
      key: "jobs",
      label: t("projectDetail.jobsTab"),
      children: <JobsPanel projectId={id} canEdit={canRunJobs} />,
    },
    {
      key: "files",
      label: t("projectDetail.filesTab"),
      children: <FilesPanel projectId={id} inputFileType={p.input_file_type} canEdit={canEditFiles} />,
    },
    {
      key: "query",
      label: t("projectDetail.queryTab"),
      // Tab visible to every member; the backend still enforces viewer+ on
      // the stream (canUse is viewer+ — currently always true).
      children: <QueryPanel projectId={id} canUse />,
    },
    {
      key: "explore",
      label: t("projectDetail.exploreTab"),
      // Same gating as Query: every member can browse the indexed artifacts.
      children: <ExplorePanel projectId={id} canUse />,
    },
  ];

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>{p.name}</Typography.Title>
      <Tabs items={items} />
    </div>
  );
}
