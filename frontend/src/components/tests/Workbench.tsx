import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Modal, Segmented, Select, Space, Typography, message,
} from "antd";
import { api, detailOf } from "../../api/client";
import type { MatrixRow, Question, QuestionSet, QueryMethod, TestRun } from "../../api/types";
import AdhocQuery from "./AdhocQuery";
import RatingMatrix from "./RatingMatrix";
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
  const [regressionsOnly, setRegressionsOnly] = useState(false);

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
  // eagerly while it is selected.
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
      if (!r.ok) throw new Error(await detailOf(r, "jobs.loadPreflightFailed"));
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
    retry: false,
    refetchInterval: activeJob?.type === "test_run" ? 5000 : false,
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
      qc.invalidateQueries({ queryKey: ["projects", projectId, "test-runs"] });
      qc.invalidateQueries({ queryKey: ["projects", projectId, "jobs", "preflight"] });
    },
    onError: (e) => message.error(e.message),
  });

  const questionCount = questions.data ? questions.data.questions.length : null;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Segmented
        value={mode}
        onChange={(v) => setMode(v as "matrix" | "adhoc")}
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
              onChange={setSetId}
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

          <RatingMatrix
            runs={matrix.data?.runs ?? []}
            rows={matrix.data?.rows ?? []}
            regressionsOnly={regressionsOnly}
            onRegressionsOnly={setRegressionsOnly}
          />
        </>
      )}
    </Space>
  );
}
