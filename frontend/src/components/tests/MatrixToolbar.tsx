import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Modal, Select, Space, message } from "antd";
import { sendOk } from "../../api/client";
import { questionSets } from "../../api/queries";
import type { QuestionSet, QueryMethod } from "../../api/types";
import { methodOptions } from "./methods";
import type { NameDialogKind } from "./SetNameDialog";

// The matrix mode's top row: set picker and set curation (canEdit), the
// run method, and the launch button.
export default function MatrixToolbar({
  projectId, sets, setsLoading, setId, onSetChange, onArchived, onNameDialog,
  method, onMethod, canEdit, canLaunch, firstRun, onLaunch,
}: {
  projectId: string;
  sets: QuestionSet[];
  setsLoading: boolean;
  setId: string | undefined;
  onSetChange: (id: string) => void;
  onArchived: () => void;
  onNameDialog: (kind: NameDialogKind) => void;
  method: QueryMethod;
  onMethod: (m: QueryMethod) => void;
  canEdit: boolean;
  canLaunch: boolean;
  firstRun: boolean;
  onLaunch: () => void;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const archiveSet = useMutation({
    mutationFn: (sid: string) => sendOk(`/api/projects/${projectId}/question-sets/${sid}`,
      "workbench.archiveSetFailed", { method: "DELETE" }),
    onSuccess: () => {
      message.success(t("workbench.setArchived"));
      onArchived();
      void qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
    },
  });
  const confirmArchiveSet = () => {
    const sid = setId;
    if (!sid) return;
    Modal.confirm({
      title: t("workbench.archiveSet"),
      content: t("workbench.archiveSetConfirm", {
        name: sets.find((s) => s.id === sid)?.name ?? "",
      }),
      okText: t("workbench.archive"),
      okButtonProps: { danger: true },
      cancelText: t("common.cancel"),
      onOk: () => archiveSet.mutateAsync(sid),
    });
  };

  return (
    <Space wrap>
      <Select
        style={{ minWidth: 220 }}
        placeholder={t("workbench.setPlaceholder")}
        value={setId}
        options={sets.map((s) => ({ label: s.name, value: s.id }))}
        onChange={onSetChange}
        loading={setsLoading}
      />
      {canEdit && (
        <>
          <Button onClick={() => onNameDialog("create")}>{t("workbench.newSet")}</Button>
          <Button disabled={!setId} onClick={() => onNameDialog("rename")}>
            {t("workbench.renameSet")}
          </Button>
          <Button danger disabled={!setId} onClick={confirmArchiveSet}>
            {t("workbench.archiveSet")}
          </Button>
        </>
      )}
      <Select
        style={{ width: 140 }}
        value={method}
        onChange={onMethod}
        options={methodOptions(t)}
      />
      <Button type="primary" disabled={!canLaunch} onClick={onLaunch}>
        {t(firstRun ? "workbench.runSet" : "workbench.rerunSet")}
      </Button>
    </Space>
  );
}
