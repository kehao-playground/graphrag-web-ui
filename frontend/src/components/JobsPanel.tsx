import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Modal, Popconfirm, Select, Space, Typography, message,
} from "antd";
import { apiJson, sendOk } from "../api/client";
import { jobsPreflight, projectHealth, projectJobs, projectJobsKey } from "../api/queries";
import type { CacheClear } from "../api/types";
import { jobMethodLabel, jobTypeLabel, jobTypeShortLabel } from "./labels";
import JobLogViewer from "./JobLogViewer";
import JobsTable from "./jobs/JobsTable";
import { formatDateTime } from "../i18n/format";
import { humanBytes } from "./files/indexState";

// Rows per page of the history; the server pages it (R3-10).
const PAGE_SIZE = 20;

export default function JobsPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  // JobOut types method/type as plain strings; the lookups cover the known
  // values and the fallback shows unknowns raw.
  const typeLabel = (v: string) => jobTypeShortLabel(v, t);
  const methodLabel = (v: string) => jobMethodLabel(v, t);
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

  const rows = jobs.data?.items ?? [];

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

  // The launch warning's way out (R3-25); the backend refuses while a job
  // is active, and the button is hidden then anyway.
  const clearCache = useMutation({
    mutationFn: () => apiJson<CacheClear>(
      `/api/projects/${projectId}/cache:clear`, "jobs.clearCacheFailed", { method: "POST" },
    ),
    onSuccess: (r) => {
      message.success(t("jobs.cacheCleared", { freed: humanBytes(r.freed_bytes) }));
      void invalidateJobs();
    },
  });


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
  const pf = preflight.data;
  const cacheOver = !!pf && pf.cache_bytes > pf.cache_quota_mb * 1024 * 1024;
  // Enqueue refuses above the quota (409 quota_exceeded), so Start does too.
  const overQuota = !!pf && pf.usage_bytes > pf.project_quota_mb * 1024 * 1024;

  const confirmLaunch = () => {
    const scope = scopeLine();
    const last = pf?.last_run ?? null;
    const diskLow = !!pf && pf.disk_free_mb < pf.disk_watermark_mb;
    Modal.confirm({
      title: t("jobs.confirmTitle", { type: typeLabel(type) }),
      content: (
        <Space orientation="vertical" style={{ width: "100%" }}>
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
              message={t("jobs.cacheOver", { used: humanBytes(pf.cache_bytes), quota: humanBytes(pf.cache_quota_mb * 1024 * 1024) })}
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


  // The drawer names its job when the list has it; a job outside the list
  // (a test run from the overview's link) keeps the generic title.
  const logJob = rows.find((j) => j.id === logJobId);
  const logTitle = logJob
    ? t(logJob.started_at ? "jobs.logsTitleStarted" : "jobs.logsTitleQueued", {
      type: typeLabel(logJob.type),
      method: methodLabel(logJob.method),
      time: formatDateTime(logJob.started_at ?? logJob.queued_at, i18n.language),
    })
    : t("jobs.logsTitle");

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {cliMissing && <Alert type="error" showIcon message={t("jobs.graphragMissing")} />}
      {activeJob && (
        <Alert
          type="warning"
          showIcon
          message={t("jobs.activeJobNotice", { type: jobTypeLabel(activeJob.type, t) })}
        />
      )}
      {overQuota && pf && (
        <Alert
          type="error"
          showIcon
          message={t("jobs.overQuota", {
            used: humanBytes(pf.usage_bytes), quota: humanBytes(pf.project_quota_mb * 1024 * 1024),
          })}
        />
      )}
      {cacheOver && pf && (
        <Alert
          type="warning"
          showIcon
          message={t("jobs.cacheOverNotice", {
            used: humanBytes(pf.cache_bytes), quota: humanBytes(pf.cache_quota_mb * 1024 * 1024),
          })}
          action={canEdit && !activeJob && (
            <Popconfirm
              title={t("jobs.clearCacheConfirm")}
              okText={t("jobs.clearCache")}
              cancelText={t("common.cancel")}
              onConfirm={() => clearCache.mutate()}
            >
              <Button size="small" loading={clearCache.isPending}>{t("jobs.clearCache")}</Button>
            </Popconfirm>
          )}
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
          disabled={!canEdit || activeJob !== null || cliMissing || overQuota}
          loading={startJob.isPending}
          onClick={confirmLaunch}
        >
          {t("jobs.startIndex")}
        </Button>
      </Space>
      <JobsTable
        rows={rows}
        loading={jobs.isPending}
        page={page}
        pageSize={PAGE_SIZE}
        total={jobs.data?.total ?? 0}
        onPage={setPage}
        canEdit={canEdit}
        onLogs={setLogJobId}
        onCancel={(id) => cancelJob.mutate(id)}
      />
      <JobLogViewer jobId={logJobId} title={logTitle} onClose={() => setLogJobId(null)} />
    </Space>
  );
}
