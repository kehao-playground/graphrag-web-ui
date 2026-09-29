import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { sendOk } from "../api/client";
import { jobsPreflight, projectHealth, projectJobs, projectJobsKey } from "../api/queries";
import { JobStatusColor } from "../api/types";
import type { Job } from "../api/types";
import { i18n } from "../i18n";
import { jobStatusLabel, jobTypeLabel, jobTypeShortLabel } from "./labels";
import JobLogViewer from "./JobLogViewer";


// Humanized duration for the duration column; at most two units.
// Module-level helper outside the component: reads i18n directly, no hook.
function humanDuration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return i18n.t("jobs.durationSeconds", { s });
  const m = Math.floor(s / 60);
  if (m < 60) {
    return s % 60
      ? i18n.t("jobs.durationMinutesSeconds", { m, s: s % 60 })
      : i18n.t("jobs.durationMinutes", { m });
  }
  return m % 60
    ? i18n.t("jobs.durationHoursMinutes", { h: Math.floor(m / 60), m: m % 60 })
    : i18n.t("jobs.durationHours", { h: Math.floor(m / 60) });
}

// A job still counts as active while the runner can transition it; polling
// and the cancel affordance both key off this (cancelling = cancel requested,
// a second request would 409).
const isActive = (j: Job) => ["queued", "running"].includes(j.status);

// Rows per page of the history; the server pages it (R3-10).
const PAGE_SIZE = 20;

export default function JobsPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  // JobOut types method/type as plain strings; the lookups cover the known
  // values and the fallback shows unknowns raw.
  const typeLabel = (v: string) => jobTypeShortLabel(v, t);
  const methodLabel = (v: string) =>
    v === "standard" ? t("jobs.methodStandard") : v === "fast" ? t("jobs.methodFast") : v;
  const TYPE_OPTIONS = (["index", "update"] as const).map((v) => ({ label: typeLabel(v), value: v }));
  const METHOD_OPTIONS = (["standard", "fast"] as const).map((v) => ({ label: methodLabel(v), value: v }));
  const [type, setType] = useState<"index" | "update">("index");
  const [method, setMethod] = useState<"standard" | "fast">("standard");
  // The open log drawer lives in the URL (?log=<job id>), so the
  // overview's "open the log" links land with it open.
  const [searchParams, setSearchParams] = useSearchParams();
  const logJobId = searchParams.get("log");
  const setLogJobId = (id: string | null) => setSearchParams((prev) => {
    const next = new URLSearchParams(prev);
    if (id) next.set("log", id);
    else next.delete("log");
    return next;
  });

  // The shared preflight, loud here: the launch guardrail reads it, so a
  // failure is this pane's own error rather than a missing decoration.
  const preflight = useQuery({ ...jobsPreflight(projectId), meta: { silent: false } });
  const activeJob = preflight.data?.active_job ?? null;
  // Spec §10: the launch refuses up front when the API image lost the CLI.
  const cliMissing = preflight.data?.graphrag === "not-installed";
  // The document counts the launch confirm states (R4-25).
  const health = useQuery(projectHealth(projectId));

  // Poll every 5s only while a job on this page is queued/running/
  // cancelling; otherwise the query is quiet (refetchInterval false). An
  // active job is always the newest, so page 1 is the one that polls.
  const [page, setPage] = useState(1);
  const jobs = useQuery({
    ...projectJobs(projectId, { limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE }),
    refetchInterval: (query) =>
      query.state.data?.items.some(
        (j) => ["queued", "running"].includes(j.status) || j.display_status === "cancelling",
      )
        ? 5000
        : false,
  });

  // Prefix invalidation: every page of the list and the preflight.
  const invalidateJobs = () => qc.invalidateQueries({ queryKey: projectJobsKey(projectId) });

  const startJob = useMutation({
    mutationFn: () => sendOk(`/api/projects/${projectId}/jobs`, "jobs.startFailed", {
      method: "POST",
      body: JSON.stringify({ type, method }),
    }),
    onSuccess: () => {
      message.success(t("jobs.queued"));
      void invalidateJobs();
    },
  });

  const cancelJob = useMutation({
    mutationFn: (id: string) => sendOk(`/api/jobs/${id}/cancel`, "jobs.cancelFailed", { method: "POST" }),
    onSuccess: () => {
      message.success(t("jobs.cancelRequested"));
      void invalidateJobs();
    },
  });

  // Elapsed time on active rows ticks once a second, only while one runs.
  const [now, setNow] = useState(() => Date.now());
  const rows = jobs.data?.items ?? [];
  const ticking = rows.some((j) => j.started_at && !j.finished_at);
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);

  // What the launch will cover: a full index reads every document still in
  // input/, an update only the new and modified ones.
  const scopeLine = () => {
    const f = health.data?.files;
    if (!f) return null;
    return type === "index"
      ? t("jobs.confirmIndexScope", { count: f.total - f.removed, method: methodLabel(method) })
      : t("jobs.confirmUpdateScope", { count: f.new + f.modified, method: methodLabel(method) });
  };

  // Cost guardrail: double confirm naming the scope and the billing, then
  // last-run cost + cache/disk watermarks.
  const confirmLaunch = () => {
    const pf = preflight.data;
    const scope = scopeLine();
    const last = pf?.last_run ?? null;
    const cacheOver = !!pf && pf.cache_bytes > pf.cache_quota_mb * 1024 * 1024;
    const diskLow = !!pf && pf.disk_free_mb < pf.disk_watermark_mb;
    Modal.confirm({
      title: t("jobs.confirmTitle", { type: typeLabel(type) }),
      content: (
        <Space direction="vertical" style={{ width: "100%" }}>
          {scope && <Typography.Text>{scope}</Typography.Text>}
          <Typography.Text type="secondary">{t("jobs.confirmBilling")}</Typography.Text>
          {last ? (
            <Typography.Text>
              {t("jobs.lastRun", { s: Math.round(last.total_runtime_seconds ?? 0), docs: last.num_documents ?? 0 })}
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">{t("jobs.noRuns")}</Typography.Text>
          )}
          {cacheOver && pf && (
            <Alert
              type="warning"
              showIcon
              message={t("jobs.cacheOver", { used: (pf.cache_bytes / 1024 / 1024).toFixed(0), quota: pf.cache_quota_mb })}
            />
          )}
          {diskLow && pf && (
            <Alert
              type="error"
              showIcon
              message={t("jobs.diskLow", { free: pf.disk_free_mb, watermark: pf.disk_watermark_mb })}
            />
          )}
        </Space>
      ),
      okText: t("jobs.start"),
      cancelText: t("common.cancel"),
      onOk: () => startJob.mutate(),
    });
  };

  const columns: TableProps<Job>["columns"] = [
    { title: t("jobs.type"), dataIndex: "type", width: 80, render: (_, j) => typeLabel(j.type) },
    { title: t("jobs.method"), dataIndex: "method", width: 80, render: (_, j) => methodLabel(j.method) },
    {
      title: t("common.status"),
      dataIndex: "display_status",
      width: 140,
      render: (_, j) => (
        <Tag color={JobStatusColor[j.display_status] ?? "default"}>{jobStatusLabel(j.display_status, t)}</Tag>
      ),
    },
    { title: t("jobs.exitCode"), dataIndex: "exit_code", width: 90, render: (_, j) => (j.exit_code ?? t("common.notApplicable")) },
    {
      title: t("jobs.queuedAt"),
      dataIndex: "queued_at",
      width: 180,
      render: (_, j) => new Date(j.queued_at).toLocaleString(i18n.language),
    },
    {
      title: t("jobs.duration"),
      width: 110,
      // A running job shows its elapsed time (R4-26), a finished one its span.
      render: (_, j) =>
        j.started_at
          ? humanDuration(
            ((j.finished_at ? new Date(j.finished_at).getTime() : now) - new Date(j.started_at).getTime())
              / 1000,
          )
          : t("common.notApplicable"),
    },
    {
      title: t("common.actions"),
      width: 170,
      render: (_, j) => (
        <Space>
          <Button
            size="small"
            onClick={() => setLogJobId(j.id)}
          >
            {t("jobs.logs")}
          </Button>
          {canEdit && isActive(j) && !j.cancel_requested_at && (
            <Popconfirm title={t("jobs.cancelJobTitle")} okText={t("jobs.confirmCancel")} onConfirm={() => cancelJob.mutate(j.id)}>
              <Button danger size="small">{t("common.cancel")}</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  // The drawer names its job when the list has it; a job outside the list
  // (a test run from the overview's link) keeps the generic title.
  const logJob = rows.find((j) => j.id === logJobId);
  const logTitle = logJob
    ? t(logJob.started_at ? "jobs.logsTitleStarted" : "jobs.logsTitleQueued", {
      type: typeLabel(logJob.type),
      method: methodLabel(logJob.method),
      time: new Date(logJob.started_at ?? logJob.queued_at).toLocaleString(i18n.language),
    })
    : t("jobs.logsTitle");

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {cliMissing && <Alert type="error" showIcon message={t("jobs.graphragMissing")} />}
      {activeJob && (
        <Alert
          type="warning"
          showIcon
          message={t("jobs.activeJobNotice", { type: jobTypeLabel(activeJob.type, t) })}
        />
      )}
      <Space wrap>
        <Select
          aria-label={t("jobs.type")}
          style={{ width: 120 }}
          value={type}
          onChange={(v) => setType(v)}
          disabled={!canEdit}
          options={TYPE_OPTIONS}
        />
        <Select
          aria-label={t("jobs.method")}
          style={{ width: 120 }}
          value={method}
          onChange={(v) => setMethod(v)}
          disabled={!canEdit}
          options={METHOD_OPTIONS}
        />
        <Button
          type="primary"
          disabled={!canEdit || activeJob !== null || cliMissing}
          loading={startJob.isPending}
          onClick={confirmLaunch}
        >
          {t("jobs.startIndex")}
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={jobs.isPending}
        dataSource={rows}
        columns={columns}
        pagination={{
          current: page,
          pageSize: PAGE_SIZE,
          // Server-side: total comes from the envelope, not the page length.
          total: jobs.data?.total ?? 0,
          showSizeChanger: false,
          hideOnSinglePage: true,
          onChange: setPage,
        }}
        expandable={{
          rowExpandable: (j) => !!j.error,
          expandedRowRender: (j) => (
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, color: "#cf1322" }}>{j.error}</pre>
          ),
        }}
      />
      <JobLogViewer jobId={logJobId} open={logJobId !== null} title={logTitle} onClose={() => setLogJobId(null)} />
    </Space>
  );
}
