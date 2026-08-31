import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { Role, User } from "../api/types";
import { useAuth } from "../stores/auth";

// Built-in role names are the backend seed's closed set, so the template
// key stays inside typed-t's key union; custom roles render their raw name.
type BuiltinRoleName =
  "user_admin" | "ops" | "viewer" | "maintainer" | "editor" | "owner";

interface CreateForm {
  email: string;
  display_name: string;
  password: string;
  roles: string[];
}
interface EditForm {
  display_name: string;
  roles: string[];
}

export default function AdminUsers() {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const { user: me, authMode } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<User>();
  const [resetTarget, setResetTarget] = useState<User>();
  const [createForm] = Form.useForm<CreateForm>();
  const [editForm] = Form.useForm<EditForm>();
  const [resetForm] = Form.useForm<{ new_password: string }>();

  // Different endpoint and shape from ["users"] (the narrow GET /api/users list); keys must stay separate
  const { data: users, isPending, error } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: async () => {
      const r = await api("/api/admin/users");
      if (!r.ok) throw new Error(await detailOf(r, "projects.loadUsersFailed"));
      return (await r.json()) as User[];
    },
    retry: false,
  });

  useEffect(() => {
    if (error) message.error(error.message);
  }, [error]);

  const create = useMutation({
    mutationFn: async (v: CreateForm) => {
      const r = await api("/api/admin/users", { method: "POST", body: JSON.stringify(v) });
      if (!r.ok) throw new Error(await detailOf(r, "projects.createFailed"));
    },
    onSuccess: () => {
      message.success(t("adminUsers.created"));
      setCreateOpen(false);
      createForm.resetFields();
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => message.error(e.message),
  });

  // Grantable global roles (GET /api/roles?scope=global): every logged-in
  // user may read the catalog — names leak nothing sensitive.
  const rolesQ = useQuery({
    queryKey: ["roles", "global"],
    queryFn: async () => {
      const r = await api("/api/roles?scope=global");
      if (!r.ok) throw new Error(await detailOf(r, "adminUsers.loadRolesFailed"));
      return (await r.json()) as Role[];
    },
    retry: false,
  });

  useEffect(() => {
    if (rolesQ.error) message.error(rolesQ.error.message);
  }, [rolesQ.error]);

  const roleLabel = (name: string, isSystem: boolean) =>
    isSystem ? t(`roles.${name as BuiltinRoleName}`) : name;
  const GLOBAL_ROLE_OPTIONS = (rolesQ.data ?? []).map((r) => ({
    label: roleLabel(r.name, r.is_system), value: r.id,
  }));

  const patch = useMutation({
    mutationFn: async ({ id, ...body }: { id: string } & Partial<EditForm & { is_active: boolean }>) => {
      const r = await api(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      if (!r.ok) throw new Error(await detailOf(r, "adminUsers.updateFailed"));
    },
    onSuccess: () => {
      message.success(t("adminUsers.updated"));
      setEditTarget(undefined);
      editForm.resetFields();
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => message.error(e.message),
  });

  const resetPassword = useMutation({
    mutationFn: async ({ id, new_password }: { id: string; new_password: string }) => {
      const r = await api(`/api/admin/users/${id}/reset-password`, {
        method: "POST",
        body: JSON.stringify({ new_password }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "adminUsers.resetFailed"));
    },
    onSuccess: () => {
      message.success(t("adminUsers.resetDone"));
      setResetTarget(undefined);
      resetForm.resetFields();
    },
    onError: (e) => message.error(e.message),
  });

  const columns: TableProps<User>["columns"] = [
    { title: t("common.email"), dataIndex: "email" },
    { title: t("common.displayName"), dataIndex: "display_name" },
    {
      title: t("common.roles"),
      width: 160,
      render: (_, u) => (
        <Space size={4} wrap>
          {u.roles.length === 0 && <Tag>—</Tag>}
          {u.roles.map((r) => (
            <Tag key={r.id} color={r.name === "user_admin" ? "gold" : r.name === "ops" ? "geekblue" : undefined}>
              {roleLabel(r.name, r.is_system)}
            </Tag>
          ))}
        </Space>
      ),
    },
    {
      title: t("common.status"),
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => (v ? <Tag color="green">{t("adminUsers.active")}</Tag> : <Tag color="red">{t("adminUsers.inactive")}</Tag>),
    },
    {
      title: t("common.actions"),
      render: (_, u) => {
        // The backend always 400s changing your own role / is_active; the UI locks it out rather than offer a guaranteed failure
        const self = u.id === me?.id;
        return (
          <Space>
            <Button
              size="small"
              onClick={() => {
                setEditTarget(u);
                editForm.setFieldsValue({ display_name: u.display_name, roles: u.roles.map((r) => r.id) });
              }}
            >
              {t("adminUsers.edit")}
            </Button>
            {/* Proxy mode: the IdP owns passwords, so reset lives only in local mode (spec §5.4). */}
            {authMode !== "proxy" && (
              <Button size="small" data-testid="reset-password-button" onClick={() => setResetTarget(u)}>
                {t("adminUsers.resetPassword")}
              </Button>
            )}
            <Popconfirm
              title={u.is_active ? t("adminUsers.disableTitle", { email: u.email }) : t("adminUsers.enableTitle", { email: u.email })}
              description={t("adminUsers.disableWarning")}
              okText={u.is_active ? t("adminUsers.inactive") : t("adminUsers.active")}
              okButtonProps={u.is_active ? { danger: true } : undefined}
              onConfirm={() => patch.mutate({ id: u.id, is_active: !u.is_active })}
            >
              <Button size="small" danger={u.is_active} disabled={self}>
                {u.is_active ? t("adminUsers.inactive") : t("adminUsers.active")}
              </Button>
            </Popconfirm>
          </Space>
        );
      },
    },
  ];

  return (
    <Card style={{ marginTop: 16 }}>
      <Space style={{ marginBottom: 16, width: "100%", justifyContent: "space-between" }}>
        <Typography.Title level={4} style={{ margin: 0 }}>{t("adminUsers.pageTitle")}</Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>{t("adminUsers.createButton")}</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon message={t("adminUsers.listLoadFailed")} description={error.message} style={{ marginBottom: 16 }} />
      )}
      <Table
        rowKey="id"
        size="middle"
        loading={isPending}
        dataSource={users ?? []}
        columns={columns}
        pagination={false}
      />

      <Modal
        title={t("adminUsers.createModalTitle")}
        open={createOpen}
        okText={t("common.create")}
        cancelText={t("common.cancel")}
        confirmLoading={create.isPending}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => createForm.validateFields().then((v) => create.mutate(v))}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="email"
            label={t("common.email")}
            rules={[
              { required: true, whitespace: true, message: t("login.emailRequired") },
              { type: "email", message: t("adminUsers.emailInvalid") },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="display_name"
            label={t("common.displayName")}
            rules={[
              { required: true, whitespace: true, message: t("adminUsers.displayNameRequired") },
              { max: 100, message: t("adminUsers.displayNameMax") },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label={t("adminUsers.initialPassword")}
            extra={t("adminUsers.initialPasswordHint")}
            rules={[
              { required: true, message: t("adminUsers.initialPasswordRequired") },
              { min: 8, message: t("adminUsers.passwordMin") },
            ]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item name="roles" label={t("common.roles")} initialValue={[]}>
            <Select
              mode="multiple"
              options={GLOBAL_ROLE_OPTIONS}
              loading={rolesQ.isPending}
              placeholder={t("adminUsers.rolesPlaceholder")}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("adminUsers.editTitle", { email: editTarget?.email ?? "" })}
        open={!!editTarget}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        confirmLoading={patch.isPending}
        onCancel={() => setEditTarget(undefined)}
        onOk={() =>
          editForm.validateFields().then((v) => {
            if (!editTarget) return;
            // Your own row forbids role changes on the backend — the form's roles are display-only, never submitted
            patch.mutate(editTarget.id === me?.id
              ? { id: editTarget.id, display_name: v.display_name }
              : { id: editTarget.id, ...v });
          })
        }
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="display_name"
            label={t("common.displayName")}
            rules={[
              { required: true, whitespace: true, message: t("adminUsers.displayNameRequired") },
              { max: 100, message: t("adminUsers.displayNameMax") },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="roles" label={t("common.roles")}>
            <Select
              mode="multiple"
              options={GLOBAL_ROLE_OPTIONS}
              loading={rolesQ.isPending}
              placeholder={t("adminUsers.rolesPlaceholder")}
              disabled={editTarget?.id === me?.id}
            />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={t("adminUsers.resetTitle", { email: resetTarget?.email ?? "" })}
        open={!!resetTarget}
        okText={t("adminUsers.reset")}
        cancelText={t("common.cancel")}
        confirmLoading={resetPassword.isPending}
        onCancel={() => { setResetTarget(undefined); resetForm.resetFields(); }}
        onOk={() =>
          resetForm.validateFields().then((v) => resetTarget && resetPassword.mutate({ id: resetTarget.id, ...v }))
        }
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label={t("login.newPassword")}
            extra={t("adminUsers.resetHint")}
            rules={[
              { required: true, message: t("adminUsers.newPasswordRequired") },
              { min: 8, message: t("adminUsers.passwordMin") },
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
