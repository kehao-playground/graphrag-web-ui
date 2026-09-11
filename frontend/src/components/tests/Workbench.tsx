import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Collapse, Input, Modal, Segmented, Select, Space, Typography, message,
} from "antd";
import { api, detailOf } from "../../api/client";
import type {
  MatrixCell, MatrixRow, Question, QuestionSet, QueryMethod, TestRun,
} from "../../api/types";
import AdhocQuery from "./AdhocQuery";
import RatingMatrix from "./RatingMatrix";
import ResultDrawer from "./ResultDrawer";
import RunDiff from "./RunDiff";
import type { DiffSide } from "./RunDiff";
import { methodOptions } from "./methods";

// The retrieval test workbench (spec §9.2): one container switching between
// the rating matrix (batch test runs) and the ad-hoc query (interactive
// SSE). The routed sidebar arrives in slice ③; ProjectDetail hosts it as
// its tests tab until then.
export default function Workbench({ projectId, canUse, canRunJobs }: {
  projectId: string;
  canUse: boolean;
  canRunJobs: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [mode, setMode] = useState<"matrix" | "adhoc">("matrix");
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

  const sets = useQuery({
    queryKey: ["projects", projectId, "question-sets"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/question-sets`);
      if (!r.ok) throw new Error(await detailOf(r, "workbench.loadSetsFailed"));
      return (await r.json()) as { sets: QuestionSet[] };
    },
    retry: false,
  });
  useEffect(() => {
    if (sets.error) message.error(sets.error.message);
  }, [sets.error]);

  // Derived default: the catalog's first set until the user picks one —
  // computed during render (no effect), so a later catalog refresh keeps
  // the current choice.
  const effectiveSetId = setId ?? sets.data?.sets[0]?.id;

  // The launch dialog must state the question count pre-commit (spec
  // §7.3): SetOut carries no count, so the picker's set is fetched
  // eagerly while it is selected. The same list feeds the questions
  // section and the edit-question entry.
  const questions = useQuery({
    queryKey: ["projects", projectId, "question-sets", effectiveSetId, "questions"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/question-sets/${effectiveSetId}/questions`);
      if (!r.ok) throw new Error(await detailOf(r, "workbench.loadQuestionsFailed"));
      return (await r.json()) as { questions: Question[] };
    },
    enabled: !!effectiveSetId,
    retry: false,
  });
  useEffect(() => {
    if (questions.error) message.error(questions.error.message);
  }, [questions.error]);

  // Conflict source = the same preflight every other tab shares
  // (FilesPanel/JobsPanel cache key): active_job covers ANY queued/running
  // job — index, update or another test run all block POST /test-runs
  // (spec §7.3). Quiet on failure: a missing preflight costs the notice,
  // not the workbench; a stale miss still surfaces as the backend's 409.
  const preflight = useQuery({
    queryKey: ["projects", projectId, "jobs", "preflight"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/jobs/preflight`);
      if (!r.ok) throw new Error(await detailOf(r, "jobs.preflightFailed"));
      return (await r.json()) as { active_job: { id: string; type: string } | null };
    },
    retry: false,
  });
  const activeJob = preflight.data?.active_job ?? null;

  // The matrix window: the backend's default (5 most recent runs, oldest
  // first). Poll only while a test run actually holds the project, so the
  // cells fill in as the batch progresses.
  const matrix = useQuery({
    queryKey: ["projects", projectId, "test-runs"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/test-runs`);
      if (!r.ok) throw new Error(await detailOf(r, "workbench.loadMatrixFailed"));
      return (await r.json()) as { runs: TestRun[]; rows: MatrixRow[] };
    },
    refetchInterval: (q) =>
      q.state.data?.runs.some((r) => r.finished_at === null) ? 2000 : false,
    retry: false,
  });
  useEffect(() => {
    if (matrix.error) message.error(matrix.error.message);
  }, [matrix.error]);

  // Any active job names itself here: the label mapping covers all three
  // types so the notice never implies only indexing can block.
  const jobTypeLabel = (v: string) =>
    v === "index" ? t("workbench.typeIndex")
    : v === "update" ? t("workbench.typeUpdate")
    : v === "test_run" ? t("workbench.typeTestRun")
    : v;

  const startRun = useMutation({
    mutationFn: async () => {
      const r = await api(`/api/projects/${projectId}/test-runs`, {
        method: "POST",
        body: JSON.stringify({ set_id: effectiveSetId, method }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "workbench.startFailed"));
    },
    onSuccess: () => {
      message.success(t("workbench.queued"));
      setLaunchOpen(false);
      void qc.invalidateQueries({ queryKey: ["projects", projectId, "test-runs"] });
      void qc.invalidateQueries({ queryKey: ["projects", projectId, "jobs", "preflight"] });
    },
    onError: (e) => message.error(e.message),
  });

  // "Has runs" per the client: the lineage appears in the matrix window
  // with at least one manifested cell. The backend re-checks under the
  // project lock (spec §5.3), so a stale window can only ever under-warn.
  const hasRuns = (q: Question) =>
    (matrix.data?.rows ?? []).some(
      (r) => r.lineage_id === q.lineage_id && r.cells.some((c) => c !== null),
    );

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
      const r = await api(
        `/api/projects/${projectId}/question-sets/${effectiveSetId}/questions/${q.id}`,
        { method: "PATCH", body: JSON.stringify({ text: editText.trim() }) },
      );
      if (!r.ok) throw new Error(await detailOf(r, "workbench.saveFailed"));
    },
    onSuccess: () => {
      message.success(t("workbench.questionUpdated"));
      setEditOpen(false);
      setEditing(null);
      void qc.invalidateQueries({ queryKey: ["projects", projectId, "question-sets"] });
    },
    onError: (e) => message.error(e.message),
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Segmented
        value={mode}
        onChange={(v) => {
          setMode(v as "matrix" | "adhoc");
          clearPicks();
        }}
        options={[
          { label: t("workbench.modeMatrix"), value: "matrix" },
          { label: t("workbench.modeAdhoc"), value: "adhoc" },
        ]}
      />

      {mode === "adhoc" ? (
        <AdhocQuery projectId={projectId} canUse={canUse} />
      ) : (
        <>
          {activeJob && (
            <Alert
              type="warning"
              showIcon
              message={t("workbench.jobRunning", { type: jobTypeLabel(activeJob.type) })}
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
              {t("workbench.rerunSet")}
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
                  {(questions.data?.questions ?? []).map((q) => (
                    <Space
                      key={q.id}
                      style={{ width: "100%", justifyContent: "space-between" }}
                    >
                      <Typography.Text style={{ whiteSpace: "pre-wrap" }}>
                        {q.position}. {q.text}
                      </Typography.Text>
                      <Button
                        type="link"
                        size="small"
                        aria-label={t("workbench.editQuestionAria", { text: q.text })}
                        onClick={() => startEdit(q)}
                      >
                        {t("workbench.editQuestion")}
                      </Button>
                    </Space>
                  ))}
                  {questions.data?.questions.length === 0 && (
                    <Typography.Text type="secondary">
                      {t("workbench.questionsEmpty")}
                    </Typography.Text>
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
            onRated={() =>
              void qc.invalidateQueries({ queryKey: ["projects", projectId, "test-runs"] })
            }
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
