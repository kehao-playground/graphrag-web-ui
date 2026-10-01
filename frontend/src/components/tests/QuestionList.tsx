import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Collapse, Input, Modal, Space, Typography, message } from "antd";
import { sendOk } from "../../api/client";
import { questionSets } from "../../api/queries";
import type { MatrixRow, Question } from "../../api/types";

// The picked set's questions (spec §5.3): add, edit, archive. Editing a
// question referenced by a run forks its lineage, so the warning fires
// BEFORE the editor opens; archiving is always a soft delete.
export default function QuestionList({ projectId, setId, questions, rows, canEdit }: {
  projectId: string;
  setId: string | undefined;
  questions: Question[] | undefined;
  // The matrix window, for the client's "has runs" check.
  rows: MatrixRow[];
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const invalidateSets = () =>
    qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
  const questionUrl = (q: Question) =>
    `/api/projects/${projectId}/question-sets/${setId}/questions/${q.id}`;
  const [newQuestion, setNewQuestion] = useState("");
  // The editor is open exactly while a question is being edited (R1-113).
  const [editing, setEditing] = useState<Question | null>(null);
  const [editText, setEditText] = useState("");

  // "Has runs" per the client: the lineage appears in the matrix window
  // with at least one manifested cell. The backend re-checks under the
  // project lock (spec §5.3), so a stale window can only ever under-warn.
  const hasRuns = (q: Question) =>
    rows.some((r) => r.lineage_id === q.lineage_id && r.cells.some((c) => c !== null));

  const addQuestion = useMutation({
    mutationFn: () => sendOk(
      `/api/projects/${projectId}/question-sets/${setId}/questions`,
      "workbench.saveFailed",
      { method: "POST", body: JSON.stringify({ text: newQuestion.trim() }) },
    ),
    onSuccess: () => {
      message.success(t("workbench.questionAdded"));
      setNewQuestion("");
      void invalidateSets();
    },
  });

  // A question with runs additionally says where its history went.
  const archiveQuestion = useMutation({
    mutationFn: (q: Question) => sendOk(questionUrl(q), "workbench.archiveQuestionFailed",
      { method: "DELETE" }),
    onSuccess: () => {
      message.success(t("workbench.questionArchived"));
      void invalidateSets();
    },
  });
  const confirmArchiveQuestion = (q: Question) => {
    Modal.confirm({
      title: t("workbench.archiveQuestion"),
      content: (
        <Space orientation="vertical" size="small">
          <Typography.Text>{t("workbench.archiveQuestionConfirm")}</Typography.Text>
          {hasRuns(q) && (
            <Typography.Text type="secondary">{t("workbench.archiveQuestionHasRuns")}</Typography.Text>
          )}
        </Space>
      ),
      okText: t("workbench.archive"),
      okButtonProps: { danger: true },
      cancelText: t("common.cancel"),
      onOk: () => archiveQuestion.mutateAsync(q),
    });
  };

  const openEditor = (q: Question) => {
    setEditing(q);
    setEditText(q.text);
  };
  const startEdit = (q: Question) => {
    if (!hasRuns(q)) {
      openEditor(q);
      return;
    }
    // The fork is the point (spec §9.3/§5.3): say so BEFORE any wording
    // changes hands, and let an explicit choice continue.
    Modal.confirm({
      title: t("workbench.editQuestion"),
      content: t("workbench.editForkWarning"),
      okText: t("workbench.editContinue"),
      cancelText: t("common.cancel"),
      onOk: () => openEditor(q),
    });
  };
  const saveEdit = useMutation({
    mutationFn: async () => {
      if (!editing) return;
      await sendOk(questionUrl(editing), "workbench.saveFailed",
        { method: "PATCH", body: JSON.stringify({ text: editText.trim() }) });
    },
    onSuccess: () => {
      message.success(t("workbench.questionUpdated"));
      setEditing(null);
      void invalidateSets();
    },
  });

  return (
    <>
      <Collapse
        size="small"
        items={[{
          key: "questions",
          label: t("workbench.questionsTitle", { count: questions?.length ?? 0 }),
          children: (
            <Space orientation="vertical" size="small" style={{ width: "100%" }}>
              {(questions ?? []).map((q, i) => (
                <Space key={q.id} style={{ width: "100%", justifyContent: "space-between" }}>
                  <Typography.Text style={{ whiteSpace: "pre-wrap" }}>
                    {/* Ask order, 1-based: positions are 0-based and keep
                        gaps once a question is archived. */}
                    {i + 1}. {q.text}
                  </Typography.Text>
                  {canEdit && (
                    <Space size={0}>
                      <Button
                        type="link"
                        size="small"
                        aria-label={t("workbench.editQuestionAria", { text: q.text })}
                        onClick={() => startEdit(q)}
                      >
                        {t("workbench.editQuestion")}
                      </Button>
                      <Button
                        type="link"
                        size="small"
                        danger
                        aria-label={t("workbench.archiveQuestionAria", { text: q.text })}
                        onClick={() => confirmArchiveQuestion(q)}
                      >
                        {t("workbench.archive")}
                      </Button>
                    </Space>
                  )}
                </Space>
              ))}
              {questions?.length === 0 && (
                <Typography.Text type="secondary">{t("workbench.questionsEmpty")}</Typography.Text>
              )}
              {canEdit && setId && (
                <Space.Compact style={{ width: "100%" }}>
                  <Input
                    aria-label={t("workbench.addQuestion")}
                    placeholder={t("workbench.addQuestionPlaceholder")}
                    maxLength={2000}
                    value={newQuestion}
                    onChange={(e) => setNewQuestion(e.target.value)}
                    onPressEnter={() => {
                      if (newQuestion.trim() && !addQuestion.isPending) addQuestion.mutate();
                    }}
                  />
                  <Button
                    disabled={!newQuestion.trim()}
                    loading={addQuestion.isPending}
                    onClick={() => addQuestion.mutate()}
                  >
                    {t("workbench.addQuestion")}
                  </Button>
                </Space.Compact>
              )}
            </Space>
          ),
        }]}
      />
      <Modal
        open={editing !== null}
        title={t("workbench.editQuestion")}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        okButtonProps={{ disabled: editText.trim().length === 0 }}
        confirmLoading={saveEdit.isPending}
        onOk={() => saveEdit.mutate()}
        onCancel={() => setEditing(null)}
      >
        <Input.TextArea
          aria-label={t("workbench.editQuestion")}
          rows={3}
          maxLength={2000}
          value={editText}
          onChange={(e) => setEditText(e.target.value)}
        />
      </Modal>
    </>
  );
}
