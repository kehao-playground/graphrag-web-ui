import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { Project, UserBrief } from "../api/types";
import { useAuth } from "../stores/auth";

const FILE_TYPES: Project["input_file_type"][] = ["text", "csv", "json"];


interface CreateForm {
  name: string;
  description?: string;
  input_file_type: Project["input_file_type"];
}

export default function Projects() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { user } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateForm>();

  const { data: projects, isPending, error } = useQuery({
    queryKey: ["projects"],
    queryFn: async () => {
      const r = await api("/api/projects");
      if (!r.ok) throw new Error(await detailOf(r, `載入專案失敗(${r.status})`));
      return (await r.json()) as Project[];
    },
  });

  useEffect(() => {
    if (error) message.error(error.message);
  }, [error]);

  // Project carries only owner_id; GET /api/users is the narrow list every
  // logged-in user can call, so resolve owners through the same ['users']
  // query (as in ProjectDetail) instead of hitting each project's members (N+1).
  const users = useQuery({
    queryKey: ["users"],
    queryFn: async () => {
      const r = await api("/api/users");
      if (!r.ok) throw new Error(await detailOf(r, `載入使用者失敗(${r.status})`));
      return (await r.json()) as UserBrief[];
    },
    retry: false,
  });

  useEffect(() => {
    if (users.error) message.error(users.error.message);
  }, [users.error]);

  const ownerById = useMemo(
    () => new Map((users.data ?? []).map((u) => [u.id, u] as const)),
    [users.data],
  );

  const create = useMutation({
    mutationFn: async (v: CreateForm) => {
      const r = await api("/api/projects", { method: "POST", body: JSON.stringify(v) });
      if (!r.ok) throw new Error(await detailOf(r, `建立失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("專案已建立");
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => message.error(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const r = await api(`/api/projects/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, `刪除失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("專案已刪除");
      // Prefix invalidation: clears the list plus every project's members cache
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => message.error(e.message),
  });

  const columns: TableProps<Project>["columns"] = [
    {
      title: "名稱",
      dataIndex: "name",
      render: (_, p) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/projects/${p.id}`)}>
          {p.name}
        </Button>
      ),
    },
    {
      title: "輸入格式",
      dataIndex: "input_file_type",
      width: 110,
      render: (t: string) => <Tag>{t}</Tag>,
    },
    {
      title: "建立時間",
      dataIndex: "created_at",
      width: 210,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: "擁有者",
      render: (_, p) => {
        const o = ownerById.get(p.owner_id);
        return o ? (
          <span>
            {o.display_name} <Typography.Text type="secondary">{o.email}</Typography.Text>
          </span>
        ) : "—";
      },
    },
    {
      title: "操作",
      width: 90,
      render: (_, p) =>
        user && (user.role === "admin" || p.owner_id === user.id) ? (
          <Popconfirm
            title="刪除專案"
            description="workspace 內所有檔案將一併刪除,確定嗎?"
            okText="刪除"
            okButtonProps={{ danger: true }}
            onConfirm={() => remove.mutate(p.id)}
          >
            <Button danger size="small">刪除</Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Card style={{ marginTop: 16 }}>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>專案</Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>建立專案</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon message="無法載入專案列表" description={error.message} style={{ marginBottom: 16 }} />
      )}
      <Table
        rowKey="id"
        size="middle"
        loading={isPending}
        dataSource={projects ?? []}
        columns={columns}
        pagination={false}
      />

      <Modal
        title="建立專案"
        open={createOpen}
        okText="建立"
        cancelText="取消"
        confirmLoading={create.isPending}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.validateFields().then((v) => create.mutate(v))}
      >
        <Form form={form} layout="vertical" initialValues={{ input_file_type: "text" }}>
          <Form.Item
            name="name"
            label="名稱"
            rules={[
              { required: true, whitespace: true, message: "請輸入名稱" },
              { max: 200, message: "名稱最長 200 字" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="input_file_type" label="輸入格式" rules={[{ required: true, message: "請選擇輸入格式" }]}>
            <Select options={FILE_TYPES.map((t) => ({ label: t, value: t }))} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
