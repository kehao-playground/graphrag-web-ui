import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Descriptions, Popconfirm, Select, Space, Spin, Table, Tabs, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { Member, Project, UserBrief } from "../api/types";
import { useAuth } from "../stores/auth";
import FilesPanel from "../components/FilesPanel";
import SettingsPanel from "../components/SettingsPanel";
import JobsPanel from "../components/JobsPanel";
import QueryPanel from "../components/QueryPanel";
import ExplorePanel from "../components/ExplorePanel";

// Grantable roles only: owner is fixed to the creator (single-owner policy) and
// cannot be assigned when adding members; owner rows in the table still render it.
const ROLES = ["editor", "viewer"] as const;
type Role = (typeof ROLES)[number];
const ROLE_OPTIONS = ROLES.map((r) => ({ label: r, value: r }));


export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  const { user } = useAuth();
  const [addUserId, setAddUserId] = useState<string>();
  const [addRole, setAddRole] = useState<Role>("viewer");

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

  // Member management: system admin or project owner (the backend still
  // enforces; this only gates the UI)
  const myRole = members.data?.find((m) => m.user_id === user?.id)?.role;
  const canManage = !!user && (user.role === "admin" || myRole === "owner");

  // Content editing (upload/delete files): admin or owner/editor (Task 2 permissions)
  const canEditContent = !!user && (user.role === "admin" || myRole === "owner" || myRole === "editor");
  // Adding a member needs user_id; GET /api/users is the narrow list every
  // logged-in user can call, so a non-admin owner can pick users too
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
    mutationFn: async ({ userId, role }: { userId: string; role: Role }) => {
      const r = await api(`/api/projects/${id}/members/${userId}`, {
        method: "PUT",
        body: JSON.stringify({ role }),
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
  const owner = members.data?.find((m) => m.role === "owner");
  const memberIds = new Set(members.data?.map((m) => m.user_id));

  const memberColumns: TableProps<Member>["columns"] = [
    { title: t("common.email"), dataIndex: "email" },
    { title: t("common.displayName"), dataIndex: "display_name" },
    {
      title: t("common.role"),
      dataIndex: "role",
      width: 130,
      render: (_, m) => (
        <Select
          size="small"
          style={{ width: 110 }}
          value={m.role as Role}
          options={ROLE_OPTIONS}
          // The owner row is 400-protected on the backend; the UI locks it rather than offer a guaranteed failure
          disabled={!canManage || m.role === "owner"}
          onChange={(role) => putMember.mutate({ userId: m.user_id, role })}
        />
      ),
    },
    {
      title: t("common.actions"),
      width: 90,
      render: (_, m) =>
        canManage && m.role !== "owner" ? (
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
                  style={{ width: 110 }}
                  value={addRole}
                  options={ROLE_OPTIONS}
                  onChange={setAddRole}
                />
                <Button
                  type="primary"
                  disabled={!addUserId}
                  loading={putMember.isPending}
                  onClick={() => addUserId && putMember.mutate({ userId: addUserId, role: addRole })}
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
      children: <SettingsPanel projectId={id} canEdit={canEditContent} />,
    },
    {
      key: "jobs",
      label: t("projectDetail.jobsTab"),
      children: <JobsPanel projectId={id} canEdit={canEditContent} />,
    },
    {
      key: "files",
      label: t("projectDetail.filesTab"),
      children: <FilesPanel projectId={id} inputFileType={p.input_file_type} canEdit={canEditContent} />,
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
