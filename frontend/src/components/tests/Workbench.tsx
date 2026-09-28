import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Collapse, Empty, Input, Modal, Segmented, Select, Space, Typography, message,
} from "antd";
import { apiJson, sendOk } from "../../api/client";
import { jobsPreflight, questionSets, setQuestions, testRunMatrix } from "../../api/queries";
import type {
  MatrixCell, MatrixRow, Question, QuestionSet, QueryMethod, TestRun,
} from "../../api/types";
import { jobTypeLabel } from "../labels";
import AdhocQuery from "./AdhocQuery";
import RatingMatrix from "./RatingMatrix";
import ResultDrawer from "./ResultDrawer";
import RunDiff from "./RunDiff";
import type { DiffSide } from "./RunDiff";
import { methodOptions } from "./methods";

// The retrieval test workbench (spec §9.2): one container switching between
// the rating matrix (batch test runs) and the ad-hoc query (interactive
// SSE). The routed sidebar arrives in slice ③; ProjectDetail hosts it as
// its tests tab until then. canEdit (project:edit_content) gates curating
// sets and questions; canRunJobs gates launching a run.
export default function Workbench({ projectId, canUse, canRunJobs, canEdit }: {
  projectId: string;
  canUse: boolean;
  canRunJobs: boolean;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  // undefined until the set catalog first answers: a project without sets
  // lands on the ad-hoc query (R4-03) — the matrix has nothing to build.
  // Latched once, so creating the first set later never yanks the user
  // out of the mode they are in.
  const [mode, setMode] = useState<"matrix" | "adhoc">();
  const [setId, setSetId] = useState<string>();
  const [method, setMethod] = useState<QueryMethod>("local");
  const [launchOpen, setLaunchOpen] = useState(false);
  // The slice ③ overview's regressions card deep-links here with the
  // filter already applied (?regressions=1); after arrival the checkbox
  // keeps owning the state, so toggling stays purely local.
  const [searchParams] = useSearchParams();
  const [regressionsOnly, setRegressionsOnly] = useState(searchParams.get("regressions") === "1");

  // Cell selection (spec §9.2): the FIRST pick opens the drawer and stays
  // selected after it closes; a second pick on another cell opens the run
  // diff, and closing the diff clears the selection.
  const [selected, setSelected] = useState<{ runId: string; resultId: string } | null>(null);
  const [drawerFor, setDrawerFor] = useState<{ runId: string; resultId: string } | null>(null);
  const [diff, setDiff] = useState<{ left: DiffSide; right: DiffSide } | null>(null);
  // A selection cannot outlive the matrix it belonged to.
  const clearPicks = () => {
    setSelected(null);
    setDrawerFor(null);
    setDiff(null);
  };

  // The question editor (spec §5.3): editing a question referenced by a run
  // forks its lineage; the warning fires BEFORE the editor opens.
  const [editOpen, setEditOpen] = useState(false);
  const [editing, setEditing] = useState<Question | null>(null);
  const [editText, setEditText] = useState("");

  const sets = useQuery(questionSets(projectId));
  const noSets = sets.data?.sets.length === 0;
  if (mode === undefined && sets.data) setMode(noSets ? "adhoc" : "matrix");
  const shownMode = mode ?? "matrix";

  // Derived default: the catalog's first set until the user picks one —
  // computed during render (no effect), so a later catalog refresh keeps
  // the current choice.
  const effectiveSetId = setId ?? sets.data?.sets[0]?.id;

  // The launch dialog must state the question count pre-commit (spec
  // §7.3): SetOut carries no count, so the picker's set is fetched
  // eagerly while it is selected. The same list feeds the questions
  // section and the edit-question entry.
  const questions = useQuery({
    ...setQuestions(projectId, effectiveSetId ?? ""),
    enabled: !!effectiveSetId,
  });

  // Conflict source = the preflight every pane shares: active_job covers
  // ANY queued/running job — index, update or another test run all block
  // POST /test-runs (spec §7.3). Quiet on failure: a missing preflight
  // costs the notice, not the workbench.
  const preflight = useQuery(jobsPreflight(projectId));
  const activeJob = preflight.data?.active_job ?? null;

  // The matrix window: the backend's default (5 most recent runs, oldest
  // first). Poll only while a test run actually holds the project, so the
  // cells fill in as the batch progresses.
  const matrix = useQuery({
    ...testRunMatrix(projectId),
    refetchInterval: (q) =>
      q.state.data?.runs.some((r) => r.finished_at === null) ? 2000 : false,
  });
  const invalidateMatrix = () =>
    qc.invalidateQueries({ queryKey: testRunMatrix(projectId).queryKey });
  // The set catalog key prefixes every set's question list too.
  const invalidateSets = () =>
    qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
  const firstRun = matrix.data?.runs.length === 0;

  // Set name dialog, shared by create and rename (R1-02, R3-23).
  const [nameDialog, setNameDialog] = useState<"create" | "rename" | null>(null);
  const [setName, setSetName] = useState("");
  const openNameDialog = (kind: "create" | "rename") => {
    setSetName(kind === "rename"
      ? sets.data?.sets.find((s) => s.id === effectiveSetId)?.name ?? ""
      : "");
    setNameDialog(kind);
  };
  const saveSetName = useMutation({
    mutationFn: (kind: "create" | "rename") => {
      const body = JSON.stringify({ name: setName.trim() });
      return kind === "create"
        ? apiJson<QuestionSet>(`/api/projects/${projectId}/question-sets`,
          "workbench.createSetFailed", { method: "POST", body })
        : apiJson<QuestionSet>(`/api/projects/${projectId}/question-sets/${effectiveSetId}`,
          "workbench.renameSetFailed", { method: "PATCH", body });
    },
    onSuccess: (qs, kind) => {
      message.success(t(kind === "create" ? "workbench.setCreated" : "workbench.setRenamed"));
      setNameDialog(null);
      if (kind === "create") {
        setSetId(qs.id);
        clearPicks();
      }
      void invalidateSets();
    },
  });

  const archiveSet = useMutation({
    mutationFn: (sid: string) => sendOk(`/api/projects/${projectId}/question-sets/${sid}`,
      "workbench.archiveSetFailed", { method: "DELETE" }),
    onSuccess: () => {
      message.success(t("workbench.setArchived"));
      // Back to the derived default: the catalog's first remaining set.
      setSetId(undefined);
      clearPicks();
      void invalidateSets();
    },
  });
  const confirmArchiveSet = () => {
    const sid = effectiveSetId;
    if (!sid) return;
    Modal.confirm({
      title: t("workbench.archiveSet"),
      content: t("workbench.archiveSetConfirm", {
        name: sets.data?.sets.find((s) => s.id === sid)?.name ?? "",
      }),
      okText: t("workbench.archive"),
      okButtonProps: { danger: true },
      cancelText: t("common.cancel"),
      onOk: () => archiveSet.mutateAsync(sid),
    });
  };

  const [newQuestion, setNewQuestion] = useState("");
  const addQuestion = useMutation({
    mutationFn: () => sendOk(
      `/api/projects/${projectId}/question-sets/${effectiveSetId}/questions`,
      "workbench.saveFailed",
      { method: "POST", body: JSON.stringify({ text: newQuestion.trim() }) },
    ),
    onSuccess: () => {
      message.success(t("workbench.questionAdded"));
      setNewQuestion("");
      void invalidateSets();
    },
  });

  const startRun = useMutation({
    mutationFn: () => sendOk(`/api/projects/${projectId}/test-runs`, "workbench.startFailed", {
      method: "POST",
      body: JSON.stringify({ set_id: effectiveSetId, method }),
    }),
    onSuccess: () => {
      message.success(t("workbench.queued"));
      setLaunchOpen(false);
      void invalidateMatrix();
      void qc.invalidateQueries({ queryKey: jobsPreflight(projectId).queryKey });
    },
  });

  // "Has runs" per the client: the lineage appears in the matrix window
  // with at least one manifested cell. The backend re-checks under the
  // project lock (spec §5.3), so a stale window can only ever under-warn.
  const hasRuns = (q: Question) =>
    (matrix.data?.rows ?? []).some(
      (r) => r.lineage_id === q.lineage_id && r.cells.some((c) => c !== null),
    );

  // Archiving is always a soft delete (spec §5.3); a question with runs
  // additionally says where its history went.
  const archiveQuestion = useMutation({
    mutationFn: (q: Question) => sendOk(
      `/api/projects/${projectId}/question-sets/${effectiveSetId}/questions/${q.id}`,
      "workbench.archiveQuestionFailed", { method: "DELETE" },
    ),
    onSuccess: () => {
      message.success(t("workbench.questionArchived"));
      void invalidateSets();
    },
  });
  const confirmArchiveQuestion = (q: Question) => {
    Modal.confirm({
      title: t("workbench.archiveQuestion"),
      content: (
        <Space direction="vertical" size="small">
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
    setEditOpen(true);
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
      const q = editing;
      if (!q) return;
      await sendOk(
        `/api/projects/${projectId}/question-sets/${effectiveSetId}/questions/${q.id}`,
        "workbench.saveFailed",
        { method: "PATCH", body: JSON.stringify({ text: editText.trim() }) },
      );
    },
    onSuccess: () => {
      message.success(t("workbench.questionUpdated"));
      setEditOpen(false);
      setEditing(null);
      void qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
    },
  });

  const onCell = (run: TestRun, _row: MatrixRow, cell: MatrixCell) => {
    const pick = { runId: run.id, resultId: cell.result_id };
    if (!selected) {
      setSelected(pick);
      setDrawerFor(pick);
      return;
    }
    // Clicking the selected cell again deselects it.
    if (selected.resultId === pick.resultId) {
      setSelected(null);
      return;
    }
    const leftRun = (matrix.data?.runs ?? []).find((r) => r.id === selected.runId);
    if (!leftRun) {
      setSelected(pick);
      setDrawerFor(pick);
      return;
    }
    setDiff({
      left: { run: leftRun, resultId: selected.resultId },
      right: { run, resultId: pick.resultId },
    });
    setSelected(null);
  };

  const questionCount = questions.data ? questions.data.questions.length : null;

  // Rendered from both the no-set empty state and the picker row.
  const nameModal = () => (
    <Modal
      open={nameDialog !== null}
      title={t(nameDialog === "rename" ? "workbench.renameSet" : "workbench.createSet")}
      okText={t(nameDialog === "rename" ? "common.save" : "workbench.create")}
      cancelText={t("common.cancel")}
      okButtonProps={{ disabled: setName.trim().length === 0 }}
      confirmLoading={saveSetName.isPending}
      onOk={() => nameDialog && saveSetName.mutate(nameDialog)}
      onCancel={() => setNameDialog(null)}
    >
      <Input
        aria-label={t("workbench.setName")}
        placeholder={t("workbench.setName")}
        maxLength={200}
        value={setName}
        onChange={(e) => setSetName(e.target.value)}
        onPressEnter={() => {
          if (nameDialog && setName.trim()) saveSetName.mutate(nameDialog);
        }}
      />
    </Modal>
  );

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Segmented
        value={shownMode}
        onChange={(v) => {
          setMode(v as "matrix" | "adhoc");
          clearPicks();
        }}
        options={[
          { label: t("workbench.modeMatrix"), value: "matrix" },
          { label: t("workbench.modeAdhoc"), value: "adhoc" },
        ]}
      />

      {shownMode === "adhoc" ? (
        <AdhocQuery projectId={projectId} canUse={canUse} canEdit={canEdit} />
      ) : noSets ? (
        <>
          <Empty description={
            // Capped so the hint wraps instead of spanning a wide pane.
            <Space direction="vertical" size="small" style={{ maxWidth: 480 }}>
              <Typography.Text strong>{t("workbench.noSetsTitle")}</Typography.Text>
              <Typography.Text type="secondary">
                {t(canEdit ? "workbench.noSetsHint" : "workbench.noSetsReadOnly")}
              </Typography.Text>
            </Space>
          }>
            {canEdit && (
              <Button type="primary" onClick={() => openNameDialog("create")}>
                {t("workbench.createSet")}
              </Button>
            )}
          </Empty>
          {nameModal()}
        </>
      ) : (
        <>
          {activeJob && (
            <Alert
              type="warning"
              showIcon
              message={t("workbench.jobRunning", { type: jobTypeLabel(activeJob.type, t) })}
            />
          )}

          <Space wrap>
            <Select
              style={{ minWidth: 220 }}
              placeholder={t("workbench.setPlaceholder")}
              value={effectiveSetId}
              options={(sets.data?.sets ?? []).map((s) => ({ label: s.name, value: s.id }))}
              onChange={(v) => {
                setSetId(v);
                clearPicks();
              }}
              loading={sets.isPending}
            />
            {canEdit && (
              <>
                <Button onClick={() => openNameDialog("create")}>{t("workbench.newSet")}</Button>
                <Button disabled={!effectiveSetId} onClick={() => openNameDialog("rename")}>
                  {t("workbench.renameSet")}
                </Button>
                <Button danger disabled={!effectiveSetId} onClick={confirmArchiveSet}>
                  {t("workbench.archiveSet")}
                </Button>
              </>
            )}
            <Select
              style={{ width: 140 }}
              value={method}
              onChange={setMethod}
              options={methodOptions(t)}
            />
            <Button
              type="primary"
              disabled={!effectiveSetId || activeJob !== null || !canRunJobs}
              onClick={() => setLaunchOpen(true)}
            >
              {t(firstRun ? "workbench.runSet" : "workbench.rerunSet")}
            </Button>
          </Space>

          <Collapse
            size="small"
            items={[{
              key: "questions",
              label: t("workbench.questionsTitle", {
                count: questions.data?.questions.length ?? 0,
              }),
              children: (
                <Space direction="vertical" size="small" style={{ width: "100%" }}>
                  {(questions.data?.questions ?? []).map((q, i) => (
                    <Space
                      key={q.id}
                      style={{ width: "100%", justifyContent: "space-between" }}
                    >
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
                  {questions.data?.questions.length === 0 && (
                    <Typography.Text type="secondary">
                      {t("workbench.questionsEmpty")}
                    </Typography.Text>
                  )}
                  {canEdit && effectiveSetId && (
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
            open={launchOpen}
            title={t("workbench.launchTitle")}
            okText={t("workbench.start")}
            cancelText={t("common.cancel")}
            okButtonProps={{ disabled: questionCount === null || questionCount === 0 }}
            confirmLoading={startRun.isPending}
            onOk={() => startRun.mutate()}
            onCancel={() => setLaunchOpen(false)}
          >
            {questions.isError ? (
              <Typography.Text type="danger">{questions.error.message}</Typography.Text>
            ) : questionCount === null ? (
              <Typography.Text type="secondary">{t("common.loading")}</Typography.Text>
            ) : (
              // The raw method token is what the run records (RunOut.method)
              // and what the matrix columns will show, so the dialog states
              // the identifier, not the localized label.
              <Typography.Text>
                {t("workbench.launchSummary", { count: questionCount, method })}
              </Typography.Text>
            )}
          </Modal>

          {nameModal()}

          <Modal
            open={editOpen}
            title={t("workbench.editQuestion")}
            okText={t("common.save")}
            cancelText={t("common.cancel")}
            okButtonProps={{ disabled: editText.trim().length === 0 }}
            confirmLoading={saveEdit.isPending}
            onOk={() => saveEdit.mutate()}
            onCancel={() => setEditOpen(false)}
          >
            <Input.TextArea
              aria-label={t("workbench.editQuestion")}
              rows={3}
              maxLength={2000}
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
            />
          </Modal>

          <RatingMatrix
            runs={matrix.data?.runs ?? []}
            rows={matrix.data?.rows ?? []}
            regressionsOnly={regressionsOnly}
            onRegressionsOnly={setRegressionsOnly}
            onCell={onCell}
            selectedResultId={selected?.resultId ?? null}
          />

          {selected && (
            <Typography.Text type="secondary">
              {t("workbench.compareHint")}
            </Typography.Text>
          )}

          <ResultDrawer
            // Keyed by the picked cell: a new pick remounts the drawer with
            // fresh state instead of prop-syncing the current result in.
            key={drawerFor ? `${drawerFor.runId}:${drawerFor.resultId}` : "closed"}
            projectId={projectId}
            runId={drawerFor?.runId ?? null}
            resultId={drawerFor?.resultId ?? null}
            onClose={() => setDrawerFor(null)}
            onRated={() => void invalidateMatrix()}
          />

          <RunDiff
            open={diff !== null}
            left={diff?.left ?? null}
            right={diff?.right ?? null}
            onClose={() => setDiff(null)}
          />
        </>
      )}
    </Space>
  );
}
