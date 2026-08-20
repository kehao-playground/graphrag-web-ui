import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Button, Card, Descriptions, Empty, Popconfirm, Select, Space, Spin, Table, Tabs, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api } from "../api/client";
import type { Member, Project, UserBrief } from "../api/types";
import { useAuth } from "../stores/auth";

const ROLES = ["owner", "editor", "viewer"] as const;
type Role = (typeof ROLES)[number];
const ROLE_OPTIONS = ROLES.map((r) => ({ label: r, value: r }));
const DISABLED_TABS = [
  { key: "files", label: "Files" },
  { key: "settings", label: "Settings" },
  { key: "jobs", label: "Jobs" },
  { key: "query", label: "Query" },
  { key: "explore", label: "Explore" },
] as const;

// 後端錯誤格式固定 {"detail": "..."};owner row 保護的 400 訊息也從這裡帶出
async function detailOf(r: Response, fallback: string): Promise<string> {
  try {
    const body = await r.json() as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [addUserId, setAddUserId] = useState<string>();
  const [addRole, setAddRole] = useState<Role>("viewer");

  const project = useQuery({
    queryKey: ["projects", id],
    queryFn: async () => {
      const r = await api(`/api/projects/${id}`);
      if (!r.ok) throw new Error(await detailOf(r, `載入專案失敗(${r.status})`));
      return (await r.json()) as Project;
    },
    enabled: !!id,
    retry: false,
  });

  const members = useQuery({
    queryKey: ["projects", id, "members"],
    queryFn: async () => {
      const r = await api(`/api/projects/${id}/members`);
      if (!r.ok) throw new Error(await detailOf(r, `載入成員失敗(${r.status})`));
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

  // 成員管理權限:系統 admin 或專案 owner(後端仍會強制,這裡只控制 UI)
  const myRole = members.data?.find((m) => m.user_id === user?.id)?.role;
  const canManage = !!user && (user.role === "admin" || myRole === "owner");

  // 新增成員需要 user_id;GET /api/users 是所有已登入使用者可用的窄清單,
  // 非 admin owner 也能選人(停用者由前端過濾)
  const users = useQuery({
    queryKey: ["users"],
    queryFn: async () => {
      const r = await api("/api/users");
      if (!r.ok) throw new Error(await detailOf(r, `載入使用者失敗(${r.status})`));
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
      if (!r.ok) throw new Error(await detailOf(r, `更新成員失敗(${r.status})`));
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
      if (!r.ok) throw new Error(await detailOf(r, `移除成員失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("已移除成員");
      qc.invalidateQueries({ queryKey: ["projects", id, "members"] });
    },
    onError: (e) => message.error(e.message),
  });

  if (!id) return <Alert type="warning" showIcon message="缺少專案 ID" />;
  if (project.isPending || members.isPending) return <Spin style={{ display: "block", marginTop: 64 }} />;
  if (project.error || !project.data) {
    return (
      <Alert
        type="error"
        showIcon
        message="無法載入專案"
        description={project.error?.message ?? "請確認你具有此專案的存取權"}
        style={{ marginTop: 16 }}
      />
    );
  }

  const p = project.data;
  const owner = members.data?.find((m) => m.role === "owner");
  const memberIds = new Set(members.data?.map((m) => m.user_id));

  const memberColumns: TableProps<Member>["columns"] = [
    { title: "電子郵件", dataIndex: "email" },
    { title: "顯示名稱", dataIndex: "display_name" },
    {
      title: "角色",
      dataIndex: "role",
      width: 130,
      render: (_, m) => (
        <Select
          size="small"
          style={{ width: 110 }}
          value={m.role as Role}
          options={ROLE_OPTIONS}
          // owner row 後端 400 保護;UI 直接鎖住避免必然失敗的操作
          disabled={!canManage || m.role === "owner"}
          onChange={(role) => putMember.mutate({ userId: m.user_id, role })}
        />
      ),
    },
    {
      title: "操作",
      width: 90,
      render: (_, m) =>
        canManage && m.role !== "owner" ? (
          <Popconfirm
            title={`移除 ${m.email}?`}
            okText="移除"
            okButtonProps={{ danger: true }}
            onConfirm={() => removeMember.mutate(m.user_id)}
          >
            <Button danger size="small">移除</Button>
          </Popconfirm>
        ) : null,
    },
  ];

  const addableUsers = (users.data ?? []).filter((u) => u.is_active && !memberIds.has(u.id));

  const items = [
    {
      key: "overview",
      label: "Overview",
      children: (
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <Descriptions
            title="專案資訊"
            bordered
            size="small"
            column={2}
            items={[
              { key: "name", label: "名稱", children: p.name },
              { key: "slug", label: "Slug", children: p.slug },
              { key: "description", label: "描述", children: p.description ?? "—" },
              { key: "type", label: "輸入格式", children: <Tag>{p.input_file_type}</Tag> },
              { key: "created", label: "建立時間", children: new Date(p.created_at).toLocaleString() },
              { key: "owner", label: "擁有者", children: owner ? `${owner.display_name}(${owner.email})` : "—" },
            ]}
          />
          <Card title="成員" size="small">
            {canManage && (
              <Space style={{ marginBottom: 16 }} title="新增成員">
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder="選擇使用者"
                  style={{ minWidth: 240 }}
                  value={addUserId}
                  options={addableUsers.map((u) => ({ label: `${u.display_name}(${u.email})`, value: u.id }))}
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
                  新增
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
    ...DISABLED_TABS.map((t) => ({
      key: t.key,
      label: t.label,
      disabled: true,
      children: <Empty description="後續階段開放" />,
    })),
  ];

  return (
    <div style={{ marginTop: 16 }}>
      <Typography.Title level={4} style={{ marginTop: 0 }}>{p.name}</Typography.Title>
      <Tabs items={items} />
    </div>
  );
}
