import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Modal, Typography, message } from "antd";
import { sendOk } from "../../api/client";
import { jobsPreflight, testRunMatrix } from "../../api/queries";
import type { QueryMethod } from "../../api/types";
import { methodLabel } from "./methods";

// The run launch confirmation: it states the question count pre-commit
// (spec §7.3) — the caller passes the picked set's loaded list state.
export default function LaunchDialog({ projectId, setId, method, open, questionCount, loadError, onClose }: {
  projectId: string;
  setId: string | undefined;
  method: QueryMethod;
  open: boolean;
  // null while the set's questions are still loading.
  questionCount: number | null;
  loadError: string | null;
  onClose: () => void;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const startRun = useMutation({
    mutationFn: () => sendOk(`/api/projects/${projectId}/test-runs`, "workbench.startFailed", {
      method: "POST",
      body: JSON.stringify({ set_id: setId, method }),
    }),
    onSuccess: () => {
      message.success(t("workbench.queued"));
      onClose();
      void qc.invalidateQueries({ queryKey: testRunMatrix(projectId).queryKey });
      void qc.invalidateQueries({ queryKey: jobsPreflight(projectId).queryKey });
    },
  });

  return (
    <Modal
      open={open}
      title={t("workbench.launchTitle")}
      okText={t("workbench.start")}
      cancelText={t("common.cancel")}
      okButtonProps={{ disabled: questionCount === null || questionCount === 0 }}
      confirmLoading={startRun.isPending}
      onOk={() => startRun.mutate()}
      onCancel={onClose}
    >
      {loadError !== null ? (
        <Typography.Text type="danger">{loadError}</Typography.Text>
      ) : questionCount === null ? (
        <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
      ) : (
        // The localized label, as the matrix columns show it (runLabel);
        // the raw id is what the request sends (V-07).
        <Typography.Text>
          {t("workbench.launchSummary", { count: questionCount, method: methodLabel(method, t) })}
        </Typography.Text>
      )}
    </Modal>
  );
}
