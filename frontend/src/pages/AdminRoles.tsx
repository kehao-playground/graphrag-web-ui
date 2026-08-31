import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Checkbox, Form, Input, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { Role } from "../api/types";

// Display labels only (spec §8): every permission DECISION stays
// backend-computed; this list never gates anything.
const ATOMS_BY_SCOPE: Record<string, readonly string[]> = {
  global: ["users:manage", "projects:view_any", "projects:act_any"],
  project: ["project:view", "project:edit_content", "project:run_jobs",
            "project:edit_settings", "project:manage"],
};

// The atom set is the backend's closed catalog, so the template key stays
// inside typed-t's key union; unknown atoms render their raw name below.
type PermKey =
  | "users_manage" | "projects_view_any" | "projects_act_any" | "projects_create"
  | "project_view" | "project_edit_content" | "project_run_jobs"
  | "project_edit_settings" | "project_manage";

interface RoleForm {
  scope: "global" | "project";
  name: string;
  description: string;
  permissions: string[];
}

export default function AdminRoles() {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [editOpen, setEditOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Role>();
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<RoleForm>();
  const [editForm] = Form.useForm<Omit<RoleForm, "scope">>();

  const permLabel = (atom: string) =>
    t(`perms.${atom.replace(":", "_") as PermKey}`, atom);

  const roles = useQuery({
    queryKey: ["admin", "roles"],
    queryFn: async () => {
      const r = await api("/api/admin/roles");
      if (!r.ok) throw new Error(await detailOf(r, "adminRoles.loadFailed"));
      return (await r.json()) as Role[];
    },
  });
  useEffect(() => {
    if (roles.error) message.error(roles.error.message);
  }, [roles.error]);

  const invalidate = () =>
    qc.invalidateQueries({ queryKey: ["admin", "roles"] });

  const create = useMutation({
    mutationFn: async (v: RoleForm) => {
      const r = await api("/api/admin/roles", {
        method: "POST", body: JSON.stringify(v),
      });
      if (!r.ok) throw new Error(await detailOf(r, "adminRoles.saveFailed"));
    },
    onSuccess: () => { setCreateOpen(false); createForm.resetFields(); invalidate(); },
    onError: (e) => message.error(e.message),
  });

  const patch = useMutation({
    mutationFn: async ({ id, v }: { id: string; v: Omit<RoleForm, "scope"> }) => {
      const r = await api(`/api/admin/roles/${id}`, {
        method: "PATCH", body: JSON.stringify(v),
      });
      if (!r.ok) throw new Error(await detailOf(r, "adminRoles.saveFailed"));
    },
    onSuccess: () => { setEditOpen(false); invalidate(); },
    onError: (e) => message.error(e.message),
  });

  const remove = useMutation({
    mutationFn: async (id: string) => {
      const r = await api(`/api/admin/roles/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, "adminRoles.deleteFailed"));
    },
    onSuccess: invalidate,
    onError: (e) => message.error(e.message),  // 409 role_in_use lands here
  });

  // One editor, two forms. `Form.useWatch` reads the live values without
  // a render-prop wrapper fighting the enclosing Form.Item for control of
  // `permissions`. The scope comes from the create form's own field, but
  // from `editTarget` in the edit modal — that form has NO scope field
  // (scope is immutable), and defaulting to "global" there would offer
  // global atoms while editing a project-scoped role.
  const createScope = Form.useWatch("scope", createForm) ?? "global";
  const createPerms = Form.useWatch("permissions", createForm) ?? [];
  const editPerms = Form.useWatch("permissions", editForm) ?? [];

  const permEditor = (mode: "create" | "edit") => {
    const form = mode === "create" ? createForm : editForm;
    const scope = mode === "create" ? createScope : (editTarget?.scope ?? "global");
    const perms: string[] = mode === "create" ? createPerms : editPerms;
    return (
      <>
        <Checkbox.Group
          value={perms}
          onChange={(v) => form.setFieldValue("permissions", v)}
          options={ATOMS_BY_SCOPE[scope].map((a) => ({
            label: permLabel(a), value: a,
          }))}
        />
        {perms.includes("project:manage") && (
          <Alert style={{ marginTop: 8 }} type="warning" showIcon
                 message={t("adminRoles.manageWarning")} />
        )}
      </>
    );
  };

  const columns: TableProps<Role>["columns"] = [
    { title: t("common.name"), dataIndex: "name" },
    { title: t("adminRoles.scope"), dataIndex: "scope", width: 90,
      render: (v: string) => <Tag>{v}</Tag> },
    { title: t("common.description"), dataIndex: "description",
      render: (v: string) => v || "—" },
    { title: t("adminRoles.permissions"), dataIndex: "permissions",
      render: (v: string[]) => (
        <Space size={4} wrap>
          {v.length === 0 && <Tag>—</Tag>}
          {v.map((p) => <Tag key={p} color="blue">{permLabel(p)}</Tag>)}
        </Space>
      ) },
    { title: t("adminRoles.system"), dataIndex: "is_system", width: 80,
      render: (v: boolean) => (v ? <Tag color="gold">{t("adminRoles.builtin")}</Tag> : null) },
    { title: t("adminRoles.usage"), width: 110,
      render: (_, r) => `${r.user_count ?? 0} / ${r.member_count ?? 0}` },
    { title: t("common.actions"), width: 130,
      render: (_, r) => (
        <Space>
          <Button size="small" disabled={r.is_system}
                  onClick={() => {
                    setEditTarget(r);
                    editForm.setFieldsValue({
                      name: r.name, description: r.description,
                      permissions: r.permissions,
                    });
                    setEditOpen(true);
                  }}>
            {t("adminRoles.edit")}
          </Button>
          <Popconfirm
            title={t("adminRoles.deleteConfirm", { name: r.name })}
            okButtonProps={{ danger: true }}
            okText={t("common.delete")}
            onConfirm={() => remove.mutate(r.id)}>
            <Button size="small" danger disabled={r.is_system}>
              {t("common.delete")}
            </Button>
          </Popconfirm>
        </Space>
      ) },
  ];

  return (
    <Card style={{ marginTop: 16 }}>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {t("adminRoles.title")}
        </Typography.Title>
        <Button type="primary" onClick={() => { createForm.resetFields(); setCreateOpen(true); }}>
          {t("adminRoles.create")}
        </Button>
      </Space>
      <Table rowKey="id" size="middle" loading={roles.isPending}
             dataSource={roles.data ?? []} columns={columns}
             pagination={false} />

      <Modal title={t("adminRoles.create")} open={createOpen}
             onCancel={() => setCreateOpen(false)}
             onOk={() => createForm.submit()}
             confirmLoading={create.isPending}>
        <Form form={createForm} layout="vertical"
              initialValues={{ scope: "global", permissions: [] }}
              onFinish={(v) => create.mutate(v)}>
          <Form.Item name="scope" label={t("adminRoles.scope")}
                     rules={[{ required: true }]}>
            {/* switching scope clears the atoms so none linger cross-scope */}
            <Select onChange={() => createForm.setFieldValue("permissions", [])}
                    options={[
              { value: "global", label: t("adminRoles.scopeGlobal") },
              { value: "project", label: t("adminRoles.scopeProject") },
            ]} />
          </Form.Item>
          <Form.Item name="name" label={t("common.name")}
                     rules={[{ required: true, message: t("adminRoles.nameRequired") }]}>
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label={t("common.description")}
                     initialValue="">
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="permissions" label={t("adminRoles.permissions")}>
            {permEditor("create")}
          </Form.Item>
        </Form>
      </Modal>

      <Modal title={t("adminRoles.edit")} open={editOpen}
             onCancel={() => setEditOpen(false)}
             onOk={() => editForm.submit()}
             confirmLoading={patch.isPending}>
        <Form form={editForm} layout="vertical" initialValues={{ permissions: [] }}
              onFinish={(v) => editTarget && patch.mutate({ id: editTarget.id, v })}>
          <Form.Item name="name" label={t("common.name")}
                     rules={[{ required: true, message: t("adminRoles.nameRequired") }]}>
            <Input maxLength={50} />
          </Form.Item>
          <Form.Item name="description" label={t("common.description")}>
            <Input maxLength={200} />
          </Form.Item>
          <Form.Item name="permissions" label={t("adminRoles.permissions")}>
            {permEditor("edit")}
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
