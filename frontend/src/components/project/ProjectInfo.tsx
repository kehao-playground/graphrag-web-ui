import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Descriptions, Form, Input, Modal, Tag, message } from "antd";
import { apiJson } from "../../api/client";
import { projectById, projectsList } from "../../api/queries";
import type { Member, Project } from "../../api/types";
import { formatDateTime } from "../../i18n/format";
import { onValid } from "../onValid";

// The project facts the old overview tab showed (spec §4 moves them to
// the members pane, where ProjectInfoDescriptions now lives). A manager
// edits the name and description from here (R3-08).
export default function ProjectInfoDescriptions({ p, owner, canManage }: {
  p: Project; owner: Member | undefined; canManage: boolean;
}) {
  const { t, i18n } = useTranslation();
  const [editing, setEditing] = useState(false);
  return (
    <>
      <Descriptions
        title={t("projectDetail.infoTitle")}
        extra={canManage && (
          <Button size="small" onClick={() => setEditing(true)}>{t("projectDetail.editInfo")}</Button>
        )}
        bordered
        size="small"
        column={2}
        items={[
          { key: "name", label: t("common.name"), children: p.name },
          { key: "slug", label: t("projectDetail.slug"), children: p.slug },
          { key: "description", label: t("common.description"), children: p.description || t("common.notApplicable") },
          { key: "type", label: t("projects.inputFormat"), children: <Tag>{p.input_file_type}</Tag> },
          { key: "created", label: t("common.createdAt"), children: formatDateTime(p.created_at, i18n.language) },
          { key: "owner", label: t("projects.owner"), children: owner ? t("common.nameWithEmail", { name: owner.display_name, email: owner.email }) : t("common.notApplicable") },
        ]}
      />
      {canManage && <EditProjectModal p={p} open={editing} onClose={() => setEditing(false)} />}
    </>
  );
}

interface ProjectInfoForm { name: string; description?: string }

function EditProjectModal({ p, open, onClose }: { p: Project; open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [form] = Form.useForm<ProjectInfoForm>();

  const save = useMutation({
    mutationFn: (v: ProjectInfoForm) => apiJson<Project>(
      `/api/projects/${p.id}`, "projectDetail.updateProjectFailed",
      { method: "PATCH", body: JSON.stringify({ name: v.name.trim(), description: v.description ?? "" }) },
    ),
    onSuccess: (updated) => {
      // The PATCH answers the full project (my_permissions included), so
      // the layout's heading updates without a refetch; the list refetches.
      qc.setQueryData(projectById(p.id).queryKey, updated);
      void qc.invalidateQueries({ queryKey: projectsList().queryKey, exact: true });
      message.success(t("projectDetail.projectUpdated"));
      onClose();
    },
  });

  return (
    <Modal
      title={t("projectDetail.editInfoTitle")}
      open={open}
      okText={t("common.save")}
      cancelText={t("common.cancel")}
      confirmLoading={save.isPending}
      onCancel={onClose}
      onOk={onValid(form, (v) => save.mutate(v))}
      destroyOnHidden
    >
      <Form
        form={form}
        layout="vertical"
        initialValues={{ name: p.name, description: p.description ?? "" }}
        preserve={false}
      >
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
          <Input.TextArea rows={3} />
        </Form.Item>
      </Form>
    </Modal>
  );
}
