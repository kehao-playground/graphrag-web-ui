import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation();
  const { user } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateForm>();

  const { data: projects, isPending, error } = useQuery({
    queryKey: ["projects"],
    queryFn: async () => {
      const r = await api("/api/projects");
      if (!r.ok) throw new Error(await detailOf(r, "projects.loadFailed"));
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
      if (!r.ok) throw new Error(await detailOf(r, "projects.loadUsersFailed"));
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
      if (!r.ok) throw new Error(await detailOf(r, "projects.createFailed"));
    },
    onSuccess: () => {
      message.success(t("projects.created"));
      setCreateOpen(false);
      form.resetFields();
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => message.error(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const r = await api(`/api/projects/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, "projects.deleteFailed"));
    },
    onSuccess: () => {
      message.success(t("projects.deleted"));
      // Prefix invalidation: clears the list plus every project's members cache
      qc.invalidateQueries({ queryKey: ["projects"] });
    },
    onError: (e) => message.error(e.message),
  });

  const columns: TableProps<Project>["columns"] = [
    {
      title: t("common.name"),
      dataIndex: "name",
      render: (_, p) => (
        <Button type="link" style={{ padding: 0 }} onClick={() => navigate(`/projects/${p.id}`)}>
          {p.name}
        </Button>
      ),
    },
    {
      title: t("projects.inputFormat"),
      dataIndex: "input_file_type",
      width: 110,
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: t("common.createdAt"),
      dataIndex: "created_at",
      width: 210,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: t("projects.owner"),
      render: (_, p) => {
        const o = ownerById.get(p.owner_id);
        return o ? (
          <span>
            {o.display_name} <Typography.Text type="secondary">{o.email}</Typography.Text>
          </span>
        ) : t("common.notApplicable");
      },
    },
    {
      title: t("common.actions"),
      width: 90,
      render: (_, p) =>
        user && (user.role === "admin" || p.owner_id === user.id) ? (
          <Popconfirm
            title={t("projects.deleteTitle")}
            description={t("projects.deleteConfirm")}
            okText={t("common.delete")}
            okButtonProps={{ danger: true }}
            onConfirm={() => remove.mutate(p.id)}
          >
            <Button danger size="small">{t("common.delete")}</Button>
          </Popconfirm>
        ) : null,
    },
  ];

  return (
    <Card style={{ marginTop: 16 }}>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>{t("projects.pageTitle")}</Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>{t("projects.createButton")}</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon message={t("projects.listLoadFailed")} description={error.message} style={{ marginBottom: 16 }} />
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
        title={t("projects.createModalTitle")}
        open={createOpen}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        confirmLoading={create.isPending}
        onCancel={() => setCreateOpen(false)}
        onOk={() => form.validateFields().then((v) => create.mutate(v))}
      >
        <Form form={form} layout="vertical" initialValues={{ input_file_type: "text" }}>
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
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="input_file_type" label={t("projects.inputFormat")} rules={[{ required: true, message: t("projects.inputFormatRequired") }]}>
            <Select options={FILE_TYPES.map((ft) => ({ label: ft, value: ft }))} />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
