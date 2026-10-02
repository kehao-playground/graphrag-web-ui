import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Button, Empty, Modal, Segmented, Space, Typography, message } from "antd";
import { sendOk } from "../../api/client";
import { jobsPreflight, questionSets, setQuestions, testRunMatrix } from "../../api/queries";
import type { MatrixCell, MatrixRow, QueryMethod, TestRun } from "../../api/types";
import { jobTypeLabel } from "../labels";
import AdhocQuery from "./AdhocQuery";
import LaunchDialog from "./LaunchDialog";
import MatrixToolbar from "./MatrixToolbar";
import QuestionList from "./QuestionList";
import RatingMatrix from "./RatingMatrix";
import ResultDrawer from "./ResultDrawer";
import RunDiff from "./RunDiff";
import type { DiffSide } from "./RunDiff";
import SetNameDialog from "./SetNameDialog";
import type { NameDialogKind } from "./SetNameDialog";

// The retrieval test workbench (spec §9.2): one container switching between
// the rating matrix (batch test runs) and the ad-hoc query (interactive
// SSE). The routed sidebar arrives in slice ③; ProjectDetail hosts it as
// its tests tab until then. canEdit (project:edit_content) gates curating
// sets and questions; canRunJobs gates launching a run.
export default function Workbench({ projectId, canRunJobs, canEdit }: {
  projectId: string;
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
  const firstRun = matrix.data?.runs.length === 0;

  // Set name dialog, shared by create and rename (R1-02, R3-23).
  const [nameDialog, setNameDialog] = useState<NameDialogKind | null>(null);
  const currentSetName = sets.data?.sets.find((s) => s.id === effectiveSetId)?.name ?? "";

  // A running test run is cancelled where it is watched (R3-21, spec §7.3);
  // same endpoint and confirm copy as the jobs pane. Other job types are
  // cancelled from the jobs pane.
  const cancelRun = useMutation({
    mutationFn: (jobId: string) => sendOk(`/api/jobs/${jobId}/cancel`, "jobs.cancelFailed",
      { method: "POST" }),
    onSuccess: () => {
      message.success(t("jobs.cancelRequested"));
      void qc.invalidateQueries({ queryKey: jobsPreflight(projectId).queryKey });
      void invalidateMatrix();
    },
  });
  const confirmCancelRun = (jobId: string) => {
    Modal.confirm({
      title: t("jobs.cancelJobTitle"),
      okText: t("jobs.confirmCancel"),
      okButtonProps: { danger: true },
      cancelText: t("common.cancel"),
      onOk: () => cancelRun.mutateAsync(jobId),
    });
  };

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
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
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
        <AdhocQuery projectId={projectId} canEdit={canEdit} />
      ) : noSets ? (
        <Empty description={
          // Capped so the hint wraps instead of spanning a wide pane.
          <Space orientation="vertical" size="small" style={{ maxWidth: 480 }}>
            <Typography.Text strong>{t("workbench.noSetsTitle")}</Typography.Text>
            <Typography.Text type="secondary">
              {t(canEdit ? "workbench.noSetsHint" : "workbench.noSetsReadOnly")}
            </Typography.Text>
          </Space>
        }>
          {canEdit && (
            <Button type="primary" onClick={() => setNameDialog("create")}>
              {t("workbench.createSet")}
            </Button>
          )}
        </Empty>
      ) : (
        <>
          {activeJob && (
            <Alert
              type="warning"
              showIcon
              message={t("workbench.jobRunning", { type: jobTypeLabel(activeJob.type, t) })}
              description={activeJob.progress
                ? t("workbench.jobProgress", activeJob.progress)
                : undefined}
              action={canRunJobs && activeJob.type === "test_run" && !activeJob.cancel_requested_at && (
                <Button size="small" danger onClick={() => confirmCancelRun(activeJob.id)}>
                  {t("workbench.cancelRun")}
                </Button>
              )}
            />
          )}

          <MatrixToolbar
            projectId={projectId}
            sets={sets.data?.sets ?? []}
            setsLoading={sets.isPending}
            setId={effectiveSetId}
            onSetChange={(v) => {
              setSetId(v);
              clearPicks();
            }}
            onArchived={() => {
              // Back to the derived default: the catalog's first remaining set.
              setSetId(undefined);
              clearPicks();
            }}
            onNameDialog={setNameDialog}
            method={method}
            onMethod={setMethod}
            canEdit={canEdit}
            canLaunch={!!effectiveSetId && activeJob === null && canRunJobs}
            firstRun={firstRun}
            onLaunch={() => setLaunchOpen(true)}
          />

          <QuestionList
            projectId={projectId}
            setId={effectiveSetId}
            questions={questions.data?.questions}
            rows={matrix.data?.rows ?? []}
            canEdit={canEdit}
          />

          <LaunchDialog
            projectId={projectId}
            setId={effectiveSetId}
            method={method}
            open={launchOpen}
            questionCount={questionCount}
            loadError={questions.isError ? questions.error.message : null}
            onClose={() => setLaunchOpen(false)}
          />

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
            run={(matrix.data?.runs ?? []).find((r) => r.id === drawerFor?.runId) ?? null}
            peers={matrix.data?.runs}
            resultId={drawerFor?.resultId ?? null}
            onClose={() => setDrawerFor(null)}
            onRated={() => void invalidateMatrix()}
          />

          <RunDiff pair={diff} peers={matrix.data?.runs} onClose={() => setDiff(null)} />
        </>
      )}

      {/* Keyed by kind: each opening starts from the current name. */}
      <SetNameDialog
        key={nameDialog ?? "closed"}
        projectId={projectId}
        setId={effectiveSetId}
        kind={nameDialog}
        initialName={nameDialog === "rename" ? currentSetName : ""}
        onClose={() => setNameDialog(null)}
        onCreated={(qs) => {
          setSetId(qs.id);
          clearPicks();
        }}
      />
    </Space>
  );
}
