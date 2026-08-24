import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import {
  Alert, Button, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import { JobStatusColor } from "../api/types";
import type { Job, Preflight } from "../api/types";
import { i18n } from "../i18n";
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

export default function JobsPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  // JobOut types method/type as plain strings; the lookups cover the known
  // values and the fallback shows unknowns raw.
  const typeLabel = (v: string) =>
    v === "index" ? t("jobs.typeIndex") : v === "update" ? t("jobs.typeUpdate") : v;
  const methodLabel = (v: string) =>
    v === "standard" ? t("jobs.methodStandard") : v === "fast" ? t("jobs.methodFast") : v;
  const TYPE_OPTIONS = (["index", "update"] as const).map((v) => ({ label: typeLabel(v), value: v }));
  const METHOD_OPTIONS = (["standard", "fast"] as const).map((v) => ({ label: methodLabel(v), value: v }));
  const [type, setType] = useState<"index" | "update">("index");
  const [method, setMethod] = useState<"standard" | "fast">("standard");
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);

  const preflight = useQuery({
    queryKey: ["projects", projectId, "jobs", "preflight"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/jobs/preflight`);
      if (!r.ok) throw new Error(await detailOf(r, "jobs.loadPreflightFailed"));
      return (await r.json()) as Preflight;
    },
    retry: false,
  });

  // Poll every 5s only while a job is queued/running/cancelling; otherwise
  // the query is quiet (refetchInterval false).
  const jobs = useQuery({
    queryKey: ["projects", projectId, "jobs"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/jobs`);
      if (!r.ok) throw new Error(await detailOf(r, "jobs.loadFailed"));
      return (await r.json()) as Job[];
    },
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.some(
        (j) => ["queued", "running"].includes(j.status) || j.display_status === "cancelling",
      )
        ? 5000
        : false,
  });

  useEffect(() => {
    if (preflight.error) message.error(preflight.error.message);
  }, [preflight.error]);
  useEffect(() => {
    if (jobs.error) message.error(jobs.error.message);
  }, [jobs.error]);

  const invalidateJobs = () => {
    qc.invalidateQueries({ queryKey: ["projects", projectId, "jobs"] });
    qc.invalidateQueries({ queryKey: ["projects", projectId, "jobs", "preflight"] });
  };

  const startJob = useMutation({
    mutationFn: async () => {
      const r = await api(`/api/projects/${projectId}/jobs`, {
        method: "POST",
        body: JSON.stringify({ type, method }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "jobs.startFailed"));
    },
    onSuccess: () => {
      message.success(t("jobs.queued"));
      invalidateJobs();
    },
    onError: (e) => message.error(e.message),
  });

  const cancelJob = useMutation({
    mutationFn: async (id: string) => {
      const r = await api(`/api/jobs/${id}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(await detailOf(r, "jobs.cancelFailed"));
    },
    onSuccess: () => {
      message.success(t("jobs.cancelRequested"));
      invalidateJobs();
    },
    onError: (e) => message.error(e.message),
  });

  // Cost guardrail: double confirm showing last-run cost + cache/disk watermarks.
  const confirmLaunch = () => {
    const pf = preflight.data;
    const last = pf?.last_run ?? null;
    const cacheOver = !!pf && pf.cache_bytes > pf.cache_quota_mb * 1024 * 1024;
    const diskLow = !!pf && pf.disk_free_mb < pf.disk_watermark_mb;
    Modal.confirm({
      title: t("jobs.confirmTitle", { type: typeLabel(type) }),
      content: (
        <Space direction="vertical" style={{ width: "100%" }}>
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
      render: (_, j) => <Tag color={JobStatusColor[j.display_status] ?? "default"}>{j.display_status}</Tag>,
    },
    { title: t("jobs.exitCode"), dataIndex: "exit_code", width: 90, render: (_, j) => (j.exit_code ?? t("common.notApplicable")) },
    {
      title: t("jobs.queuedAt"),
      dataIndex: "queued_at",
      width: 180,
      render: (_, j) => new Date(j.queued_at).toLocaleString(),
    },
    {
      title: t("jobs.duration"),
      width: 110,
      render: (_, j) =>
        j.started_at && j.finished_at
          ? humanDuration((new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000)
        : t("common.notApplicable"),
    },
    {
      title: t("common.actions"),
      width: 170,
      render: (_, j) => (
        <Space>
          <Button
            size="small"
            onClick={() => {
              setLogJobId(j.id);
              setLogOpen(true);
            }}
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

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
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
        <Button type="primary" disabled={!canEdit} loading={startJob.isPending} onClick={confirmLaunch}>
          {t("jobs.startIndex")}
        </Button>
      </Space>
      <Table
        rowKey="id"
        size="small"
        loading={jobs.isFetching}
        dataSource={jobs.data ?? []}
        columns={columns}
        pagination={false}
        expandable={{
          rowExpandable: (j) => !!j.error,
          expandedRowRender: (j) => (
            <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, color: "#cf1322" }}>{j.error}</pre>
          ),
        }}
      />
      <JobLogViewer jobId={logJobId} open={logOpen} onClose={() => setLogOpen(false)} />
    </Space>
  );
}
