import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { sendOk } from "../api/client";
import { projectsHealth, projectsList } from "../api/queries";
import type { Project } from "../api/types";
import { onValid } from "../components/onValid";
import { formatDateTime } from "../i18n/format";


const FILE_TYPES: Project["input_file_type"][] = ["text", "csv", "json"];


interface CreateForm {
  name: string;
  description?: string;
  input_file_type: Project["input_file_type"];
}

export default function Projects() {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  const [createOpen, setCreateOpen] = useState(false);
  const [form] = Form.useForm<CreateForm>();

  const { data: projects, isPending, error } = useQuery(projectsList());

  // Batch requests for the whole visible list (spec §7.5), 200 ids each; a
  // project whose chunk failed reads "health unavailable" in its cell, and
  // the per-project overview owns the detail (see projectsHealth).
  const ids = useMemo(() => (projects ?? []).map((p) => p.id).join(","), [projects]);
  const health = useQuery(projectsHealth(ids));

  const create = useMutation({
    mutationFn: (v: CreateForm) =>
      sendOk("/api/projects", "projects.createFailed", { method: "POST", body: JSON.stringify(v) }),
    onSuccess: () => {
      message.success(t("projects.created"));
      setCreateOpen(false);
      form.resetFields();
      void qc.invalidateQueries({ queryKey: projectsList().queryKey });
    },
  });

  const remove = useMutation({
    mutationFn: (id: string) => sendOk(`/api/projects/${id}`, "projects.deleteFailed", { method: "DELETE" }),
    onSuccess: () => {
      message.success(t("projects.deleted"));
      // Prefix invalidation: clears the list plus every per-project query
      void qc.invalidateQueries({ queryKey: projectsList().queryKey });
    },
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
      title: t("projects.indexHealth"),
      width: 160,
      render: (_, p) => {
        if (!health.data) return null;
        const h = health.data.projects[p.id];
        if (!h) {
          return <Typography.Text type="secondary">{t("projects.healthUnavailable")}</Typography.Text>;
        }
        // Flags, not a score (spec §9.3): the list says what is wrong, the
        // per-project overview enumerates and orders it. `removed` leads
        // because it is the one fault only a full index clears, and a
        // project whose only fault is removed documents must not read
        // healthy.
        const pending = h.files.new + h.files.modified;
        // last_attempt is the newest finish of any status, so a failure
        // there is newer than the last success. A cancel is the user's
        // own choice, not a fault to flag.
        const failed = h.last_attempt?.status === "failed";
        if (h.files.removed === 0 && pending === 0 && !failed) return null;
        return (
          <>
            {failed && <Tag color="red">{t("projects.healthAttemptFailed")}</Tag>}
            {h.files.removed > 0 && (
              <Tag color="red">{t("projects.healthRemoved")}</Tag>
            )}
            {pending > 0 && (
              <Tag color="gold">{t("projects.healthPending", { count: pending })}</Tag>
            )}
          </>
        );
      },
    },
    {
      title: t("common.createdAt"),
      dataIndex: "created_at",
      width: 210,
      render: (v: string) => formatDateTime(v, i18n.language),
    },
    {
      title: t("projects.owner"),
      render: (_, p) => (
        <span>
          {p.owner_display_name} <Typography.Text type="secondary">{p.owner_email}</Typography.Text>
        </span>
      ),
    },
    {
      title: t("common.actions"),
      width: 90,
      render: (_, p) =>
        // my_permissions folds in owner, ops act_any and custom
        // project:manage roles — no client-side role math (spec §8)
        p.my_permissions?.includes("project:manage") ? (
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
        // Only a loaded, empty list invites the first project (R4-30); a
        // load failure has its own alert above.
        locale={isPending || error ? undefined : {
          emptyText: (
            <Empty description={t("projects.emptyTitle")}>
              <Button type="primary" onClick={() => setCreateOpen(true)}>{t("projects.emptyCreate")}</Button>
            </Empty>
          ),
        }}
      />

      <Modal
        title={t("projects.createModalTitle")}
        open={createOpen}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        confirmLoading={create.isPending}
        onCancel={() => setCreateOpen(false)}
        onOk={onValid(form, (v) => create.mutate(v))}
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
