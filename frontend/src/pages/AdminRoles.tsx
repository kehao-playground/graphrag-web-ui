import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Checkbox, Form, Input, Modal, Popconfirm,
  Select, Space, Table, Tag, Typography,
} from "antd";
import type { TableProps } from "antd";
import { sendOk } from "../api/client";
import { adminRoles } from "../api/queries";
import type { Role } from "../api/types";
import { permLabel as permLabelOf, roleDescription, roleLabel } from "../components/labels";

// Display labels only (spec §8): every permission DECISION stays
// backend-computed; this list never gates anything.
const ATOMS_BY_SCOPE: Record<string, readonly string[]> = {
  global: ["users:manage", "projects:view_any", "projects:act_any"],
  project: ["project:view", "project:edit_content", "project:run_jobs",
            "project:edit_settings", "project:manage"],
};

interface RoleForm {
  scope: "global" | "project";
  name: string;
  description: string;
  permissions: string[];
}

export default function AdminRoles() {
  const qc = useQueryClient();
  const { t } = useTranslation();
  // The edit modal is open exactly while a role is targeted (R1-113).
  const [editTarget, setEditTarget] = useState<Role | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createForm] = Form.useForm<RoleForm>();
  const [editForm] = Form.useForm<Omit<RoleForm, "scope">>();

  const permLabel = (atom: string) => permLabelOf(atom, t);

  const roles = useQuery(adminRoles());

  const invalidate = () => qc.invalidateQueries({ queryKey: adminRoles().queryKey });

  const create = useMutation({
    mutationFn: (v: RoleForm) =>
      sendOk("/api/admin/roles", "adminRoles.saveFailed", { method: "POST", body: JSON.stringify(v) }),
    onSuccess: () => { setCreateOpen(false); createForm.resetFields(); void invalidate(); },
  });

  const patch = useMutation({
    mutationFn: ({ id, v }: { id: string; v: Omit<RoleForm, "scope"> }) =>
      sendOk(`/api/admin/roles/${id}`, "adminRoles.saveFailed",
        { method: "PATCH", body: JSON.stringify(v) }),
    onSuccess: () => { setEditTarget(null); void invalidate(); },
  });

  const remove = useMutation({
    mutationFn: (id: string) =>
      sendOk(`/api/admin/roles/${id}`, "adminRoles.deleteFailed", { method: "DELETE" }),
    // A 409 role_in_use surfaces through the shared mutation toast.
    onSuccess: () => void invalidate(),
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
    // Built-ins speak the catalog (R4-18), the same labels Admin — Users
    // shows; custom roles keep the name their author gave them.
    { title: t("common.name"), dataIndex: "name", render: (_, r) => roleLabel(r, t) },
    { title: t("adminRoles.scope"), dataIndex: "scope", width: 90,
      render: (v: string) => (
        <Tag>{v === "global" ? t("adminRoles.scopeGlobalShort")
          : v === "project" ? t("adminRoles.scopeProjectShort") : v}</Tag>
      ) },
    { title: t("common.description"), dataIndex: "description",
      render: (_, r) => roleDescription(r, t) || "—" },
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
    // Built-ins are immutable: no greyed-out buttons on their rows.
    { title: t("common.actions"), width: 130,
      render: (_, r) => r.is_system ? null : (
        <Space>
          <Button size="small"
                  onClick={() => {
                    setEditTarget(r);
                    editForm.setFieldsValue({
                      name: r.name, description: r.description,
                      permissions: r.permissions,
                    });
                  }}>
            {t("adminRoles.edit")}
          </Button>
          <Popconfirm
            title={t("adminRoles.deleteConfirm", { name: r.name })}
            okButtonProps={{ danger: true }}
            okText={t("common.delete")}
            onConfirm={() => remove.mutate(r.id)}>
            <Button size="small" danger>
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

      <Modal title={t("adminRoles.edit")} open={editTarget !== null}
             onCancel={() => setEditTarget(null)}
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
