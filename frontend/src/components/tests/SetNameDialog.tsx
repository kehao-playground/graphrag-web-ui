import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Input, Modal, message } from "antd";
import { apiJson } from "../../api/client";
import { questionSets } from "../../api/queries";
import type { QuestionSet } from "../../api/types";

export type NameDialogKind = "create" | "rename";

// Set name dialog, shared by create and rename (R1-02, R3-23). The caller
// keys it by kind, so each opening starts from initialName.
export default function SetNameDialog({ projectId, setId, kind, initialName, onClose, onCreated }: {
  projectId: string;
  // The set a rename applies to.
  setId: string | undefined;
  kind: NameDialogKind | null;
  initialName: string;
  onClose: () => void;
  onCreated: (set: QuestionSet) => void;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [name, setName] = useState(initialName);
  const save = useMutation({
    mutationFn: (k: NameDialogKind) => {
      const body = JSON.stringify({ name: name.trim() });
      return k === "create"
        ? apiJson<QuestionSet>(`/api/projects/${projectId}/question-sets`,
          "workbench.createSetFailed", { method: "POST", body })
        : apiJson<QuestionSet>(`/api/projects/${projectId}/question-sets/${setId}`,
          "workbench.renameSetFailed", { method: "PATCH", body });
    },
    onSuccess: (qs, k) => {
      message.success(t(k === "create" ? "workbench.setCreated" : "workbench.setRenamed"));
      onClose();
      if (k === "create") onCreated(qs);
      void qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
    },
  });

  return (
    <Modal
      open={kind !== null}
      title={t(kind === "rename" ? "workbench.renameSet" : "workbench.createSet")}
      okText={t(kind === "rename" ? "common.save" : "workbench.create")}
      cancelText={t("common.cancel")}
      okButtonProps={{ disabled: name.trim().length === 0 }}
      confirmLoading={save.isPending}
      onOk={() => kind && save.mutate(kind)}
      onCancel={onClose}
    >
      <Input
        aria-label={t("workbench.setName")}
        placeholder={t("workbench.setName")}
        maxLength={200}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onPressEnter={() => {
          if (kind && name.trim()) save.mutate(kind);
        }}
      />
    </Modal>
  );
}
