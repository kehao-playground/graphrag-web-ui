import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { User } from "../api/types";
import { useAuth } from "../stores/auth";

const ROLE_OPTIONS = (["admin", "user"] as const).map((r) => ({ label: r, value: r }));


interface CreateForm {
  email: string;
  display_name: string;
  password: string;
}
interface EditForm {
  display_name: string;
  role: User["role"];
}

export default function AdminUsers() {
  const qc = useQueryClient();
  const { user: me } = useAuth();
  const [createOpen, setCreateOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<User>();
  const [resetTarget, setResetTarget] = useState<User>();
  const [createForm] = Form.useForm<CreateForm>();
  const [editForm] = Form.useForm<EditForm>();
  const [resetForm] = Form.useForm<{ new_password: string }>();

  // 與 ["users"](GET /api/users 的窄清單)是不同端點、不同形狀,鍵必須分開
  const { data: users, isPending, error } = useQuery({
    queryKey: ["admin", "users"],
    queryFn: async () => {
      const r = await api("/api/admin/users");
      if (!r.ok) throw new Error(await detailOf(r, `載入使用者失敗(${r.status})`));
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
      if (!r.ok) throw new Error(await detailOf(r, `建立失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("使用者已建立");
      setCreateOpen(false);
      createForm.resetFields();
      qc.invalidateQueries({ queryKey: ["admin", "users"] });
    },
    onError: (e) => message.error(e.message),
  });

  const patch = useMutation({
    mutationFn: async ({ id, ...body }: { id: string } & Partial<EditForm & { is_active: boolean }>) => {
      const r = await api(`/api/admin/users/${id}`, { method: "PATCH", body: JSON.stringify(body) });
      if (!r.ok) throw new Error(await detailOf(r, `更新失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("已更新使用者");
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
      if (!r.ok) throw new Error(await detailOf(r, `重設密碼失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("密碼已重設");
      setResetTarget(undefined);
      resetForm.resetFields();
    },
    onError: (e) => message.error(e.message),
  });

  const columns: TableProps<User>["columns"] = [
    { title: "電子郵件", dataIndex: "email" },
    { title: "顯示名稱", dataIndex: "display_name" },
    {
      title: "角色",
      dataIndex: "role",
      width: 100,
      render: (v: User["role"]) => (v === "admin" ? <Tag color="gold">admin</Tag> : <Tag>user</Tag>),
    },
    {
      title: "狀態",
      dataIndex: "is_active",
      width: 100,
      render: (v: boolean) => (v ? <Tag color="green">啟用</Tag> : <Tag color="red">停用</Tag>),
    },
    {
      title: "操作",
      render: (_, u) => {
        // 改自己的 role / is_active 後端一律 400;UI 直接鎖住避免必然失敗的操作
        const self = u.id === me?.id;
        return (
          <Space>
            <Button
              size="small"
              onClick={() => {
                setEditTarget(u);
                editForm.setFieldsValue({ display_name: u.display_name, role: u.role });
              }}
            >
              編輯
            </Button>
            <Button size="small" onClick={() => setResetTarget(u)}>重設密碼</Button>
            <Popconfirm
              title={u.is_active ? `停用 ${u.email}?` : `啟用 ${u.email}?`}
              description="停用會撤銷該使用者所有已發出的 token"
              okText={u.is_active ? "停用" : "啟用"}
              okButtonProps={u.is_active ? { danger: true } : undefined}
              onConfirm={() => patch.mutate({ id: u.id, is_active: !u.is_active })}
            >
              <Button size="small" danger={u.is_active} disabled={self}>
                {u.is_active ? "停用" : "啟用"}
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
        <Typography.Title level={4} style={{ margin: 0 }}>使用者管理</Typography.Title>
        <Button type="primary" onClick={() => setCreateOpen(true)}>建立使用者</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon message="無法載入使用者列表" description={error.message} style={{ marginBottom: 16 }} />
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
        title="建立使用者"
        open={createOpen}
        okText="建立"
        cancelText="取消"
        confirmLoading={create.isPending}
        onCancel={() => { setCreateOpen(false); createForm.resetFields(); }}
        onOk={() => createForm.validateFields().then((v) => create.mutate(v))}
      >
        <Form form={createForm} layout="vertical">
          <Form.Item
            name="email"
            label="電子郵件"
            rules={[
              { required: true, whitespace: true, message: "請輸入電子郵件" },
              { type: "email", message: "電子郵件格式不正確" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="display_name"
            label="顯示名稱"
            rules={[
              { required: true, whitespace: true, message: "請輸入顯示名稱" },
              { max: 100, message: "顯示名稱最長 100 字" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密碼"
            extra="使用者下次登入將被要求更換密碼"
            rules={[
              { required: true, message: "請輸入初始密碼" },
              { min: 8, message: "密碼至少 8 字元" },
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`編輯 — ${editTarget?.email ?? ""}`}
        open={!!editTarget}
        okText="儲存"
        cancelText="取消"
        confirmLoading={patch.isPending}
        onCancel={() => setEditTarget(undefined)}
        onOk={() =>
          editForm.validateFields().then((v) => {
            if (!editTarget) return;
            // 自己的 row 後端禁止改 role — 表單裡的 role 只供顯示,不送出
            patch.mutate(editTarget.id === me?.id
              ? { id: editTarget.id, display_name: v.display_name }
              : { id: editTarget.id, ...v });
          })
        }
      >
        <Form form={editForm} layout="vertical">
          <Form.Item
            name="display_name"
            label="顯示名稱"
            rules={[
              { required: true, whitespace: true, message: "請輸入顯示名稱" },
              { max: 100, message: "顯示名稱最長 100 字" },
            ]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="role" label="角色" rules={[{ required: true, message: "請選擇角色" }]}>
            <Select options={ROLE_OPTIONS} disabled={editTarget?.id === me?.id} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={`重設密碼 — ${resetTarget?.email ?? ""}`}
        open={!!resetTarget}
        okText="重設"
        cancelText="取消"
        confirmLoading={resetPassword.isPending}
        onCancel={() => { setResetTarget(undefined); resetForm.resetFields(); }}
        onOk={() =>
          resetForm.validateFields().then((v) => resetTarget && resetPassword.mutate({ id: resetTarget.id, ...v }))
        }
      >
        <Form form={resetForm} layout="vertical">
          <Form.Item
            name="new_password"
            label="新密碼"
            extra="重設後該使用者的所有 token 會被撤銷,下次登入需更換密碼"
            rules={[
              { required: true, message: "請輸入新密碼" },
              { min: 8, message: "密碼至少 8 字元" },
            ]}
          >
            <Input.Password />
          </Form.Item>
        </Form>
      </Modal>
    </Card>
  );
}
