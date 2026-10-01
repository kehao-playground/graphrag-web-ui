import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Modal, Popconfirm, Select, Space, Table, message } from "antd";
import type { TableProps } from "antd";
import { sendOk } from "../../api/client";
import { projectMembers, roleCatalog, usersBrief } from "../../api/queries";
import type { Member, Project } from "../../api/types";
import { permLabel, roleLabel } from "../labels";
import ProjectInfoDescriptions from "./ProjectInfo";

// Atoms a role change must confirm before granting (R4-16): settings and
// API keys, and control of the project itself.
const SENSITIVE_ATOMS = ["project:edit_settings", "project:manage"];

type PutMember = { userId: string; roleId: string; email?: string };

// Members and project info (spec §4). Its reads run only while the pane is
// mounted (R1-56): the users list and role catalog are for the add bar and
// the role selects, nothing else in the project needs them.
export default function MembersPane({ project, canManage }: { project: Project; canManage: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [modal, modalHolder] = Modal.useModal();
  const id = project.id;
  const [addUserId, setAddUserId] = useState<string>();
  const [pickedRole, setPickedRole] = useState<string>();

  const members = useQuery(projectMembers(id));
  const rolesQ = useQuery(roleCatalog("project"));
  // Adding a member needs user_id; GET /api/users is the narrow list every
  // logged-in user can call, so any project:manage holder can pick users
  // (the frontend filters out disabled ones)
  const users = useQuery({ ...usersBrief(), enabled: canManage });
  const invalidateMembers = () => qc.invalidateQueries({ queryKey: projectMembers(id).queryKey });

  // owner is not grantable (single-owner policy; the owner row renders locked)
  const grantable = (rolesQ.data ?? []).filter((r) => r.name !== "owner");
  const roleOptions = grantable.map((r) => ({ label: roleLabel(r, t), value: r.id }));
  const roleById = new Map((rolesQ.data ?? []).map((r) => [r.id, r] as const));

  // The add-member role defaults to the least privileged built-in, viewer
  // (R4-16), until the manager picks one.
  const addRole = pickedRole
    ?? (grantable.find((r) => r.is_system && r.name === "viewer") ?? grantable[0])?.id;

  const putMember = useMutation({
    mutationFn: ({ userId, roleId }: PutMember) => sendOk(
      `/api/projects/${id}/members/${userId}`, "projectDetail.updateMemberFailed",
      { method: "PUT", body: JSON.stringify({ role_id: roleId }) },
    ),
    onSuccess: (_, v) => {
      if (v.email) {
        message.success(t("projectDetail.roleChanged", { email: v.email }));
      } else {
        message.success(t("projectDetail.memberAdded"));
        setAddUserId(undefined);
      }
      void invalidateMembers();
    },
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => sendOk(
      `/api/projects/${id}/members/${userId}`, "projectDetail.removeMemberFailed", { method: "DELETE" },
    ),
    onSuccess: () => {
      message.success(t("projectDetail.memberRemoved"));
      void invalidateMembers();
    },
  });

  // A change that grants settings/keys or project control asks first
  // (R4-16); any other change applies at once. Both end in a toast.
  const changeRole = (m: Member, roleId: string) => {
    const next = roleById.get(roleId);
    const had = new Set(roleById.get(m.role_id)?.permissions ?? []);
    const gained = (next?.permissions ?? []).filter((a) => SENSITIVE_ATOMS.includes(a) && !had.has(a));
    const apply = () => putMember.mutateAsync({ userId: m.user_id, roleId, email: m.email });
    if (!next || gained.length === 0) {
      void apply().catch(() => {});
      return;
    }
    modal.confirm({
      title: t("projectDetail.roleChangeTitle", { email: m.email, role: roleLabel(next, t) }),
      content: t("projectDetail.roleChangeBody", {
        perms: next.permissions.map((a) => permLabel(a, t)).join(t("common.listSeparator")),
      }),
      okText: t("projectDetail.roleChangeOk"),
      cancelText: t("common.cancel"),
      // A failed PUT toasts through the mutation cache; the dialog closes.
      onOk: () => apply().catch(() => {}),
    });
  };

  const rows = members.data ?? [];
  const owner = rows.find((m) => m.role_name === "owner");
  const memberIds = new Set(rows.map((m) => m.user_id));
  const addableUsers = (users.data ?? []).filter((u) => u.is_active && !memberIds.has(u.id));

  const columns: TableProps<Member>["columns"] = [
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
            : roleOptions}
          // The owner row is 400-protected on the backend; the UI locks it rather than offer a guaranteed failure
          disabled={!canManage || m.role_name === "owner"}
          onChange={(roleId) => changeRole(m, roleId)}
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

  return (
    <>
      {modalHolder}
      <ProjectInfoDescriptions p={project} owner={owner} canManage={canManage} />
      {canManage && (
        <Space style={{ marginBottom: 16 }} title={t("projectDetail.addMember")}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder={t("projectDetail.selectUser")}
            style={{ minWidth: 240 }}
            value={addUserId}
            options={addableUsers.map((u) => ({ label: t("common.nameWithEmail", { name: u.display_name, email: u.email }), value: u.id }))}
            onChange={setAddUserId}
            loading={users.isPending}
          />
          <Select
            style={{ width: 140 }}
            value={addRole}
            options={roleOptions}
            onChange={setPickedRole}
          />
          <Button
            type="primary"
            disabled={!addUserId || !addRole}
            loading={putMember.isPending && !putMember.variables?.email}
            onClick={() => {
              if (addUserId && addRole) putMember.mutate({ userId: addUserId, roleId: addRole });
            }}
          >
            {t("projectDetail.add")}
          </Button>
        </Space>
      )}
      <Table
        rowKey="user_id"
        size="small"
        loading={members.isFetching}
        dataSource={rows}
        columns={columns}
        pagination={false}
      />
    </>
  );
}
