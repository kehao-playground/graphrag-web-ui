import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Button, Modal, Popconfirm, Select, Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api } from "../api/client";
import { JobStatusColor } from "../api/types";
import type { Job, Preflight } from "../api/types";
import JobLogViewer from "./JobLogViewer";

// Backend errors are always {"detail": "..."}; 409 conflict / watermark
// messages from the jobs endpoints surface through here.
async function detailOf(r: Response, fallback: string): Promise<string> {
  try {
    const body = await r.json() as { detail?: string };
    return body.detail ?? fallback;
  } catch {
    return fallback;
  }
}

// JobOut types method/type as plain strings; the maps cover the known values
// and the render fallback shows unknowns raw.
const TYPE_LABEL: Record<string, string> = { index: "索引", update: "更新" };
const METHOD_LABEL: Record<string, string> = { standard: "標準", fast: "快速" };
const TYPE_OPTIONS = (["index", "update"] as const).map((v) => ({ label: TYPE_LABEL[v], value: v }));
const METHOD_OPTIONS = (["standard", "fast"] as const).map((v) => ({ label: METHOD_LABEL[v], value: v }));

// Seconds → zh-TW humanized duration (耗時 column); at most two units.
function humanDuration(seconds: number): string {
  const s = Math.round(seconds);
  if (s < 60) return `${s} 秒`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} 分 ${s % 60} 秒` : `${m} 分`;
  return m % 60 ? `${Math.floor(m / 60)} 小時 ${m % 60} 分` : `${Math.floor(m / 60)} 小時`;
}

// A job still counts as active while the runner can transition it; polling
// and the cancel affordance both key off this (cancelling = cancel requested,
// a second request would 409).
const isActive = (j: Job) => ["queued", "running"].includes(j.status);

export default function JobsPanel({ projectId, canEdit }: { projectId: string; canEdit: boolean }) {
  const qc = useQueryClient();
  const [type, setType] = useState<"index" | "update">("index");
  const [method, setMethod] = useState<"standard" | "fast">("standard");
  const [logJobId, setLogJobId] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(false);

  const preflight = useQuery({
    queryKey: ["projects", projectId, "jobs", "preflight"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/jobs/preflight`);
      if (!r.ok) throw new Error(await detailOf(r, `載入預檢資訊失敗(${r.status})`));
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
      if (!r.ok) throw new Error(await detailOf(r, `載入任務失敗(${r.status})`));
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
      if (!r.ok) throw new Error(await detailOf(r, `啟動任務失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("任務已加入佇列");
      invalidateJobs();
    },
    onError: (e) => message.error(e.message),
  });

  const cancelJob = useMutation({
    mutationFn: async (id: string) => {
      const r = await api(`/api/jobs/${id}/cancel`, { method: "POST" });
      if (!r.ok) throw new Error(await detailOf(r, `取消失敗(${r.status})`));
    },
    onSuccess: () => {
      message.success("已請求取消");
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
      title: `確認開始${TYPE_LABEL[type]}?`,
      content: (
        <Space direction="vertical" style={{ width: "100%" }}>
          {last ? (
            <Typography.Text>
              上次執行:約 {Math.round(last.total_runtime_seconds ?? 0)} 秒、{last.num_documents ?? 0} 份文件
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">此專案尚無執行記錄</Typography.Text>
          )}
          {cacheOver && pf && (
            <Alert
              type="warning"
              showIcon
              message={`快取已超過上限(${(pf.cache_bytes / 1024 / 1024).toFixed(0)} MB / ${pf.cache_quota_mb} MB),建議先清理`}
            />
          )}
          {diskLow && pf && (
            <Alert
              type="error"
              showIcon
              message={`磁碟水位不足(剩餘 ${pf.disk_free_mb} MB 低於水位 ${pf.disk_watermark_mb} MB),任務可能失敗`}
            />
          )}
        </Space>
      ),
      okText: "開始",
      cancelText: "取消",
      onOk: () => startJob.mutate(),
    });
  };

  const columns: TableProps<Job>["columns"] = [
    { title: "類型", dataIndex: "type", width: 80, render: (_, j) => TYPE_LABEL[j.type] ?? j.type },
    { title: "方法", dataIndex: "method", width: 80, render: (_, j) => METHOD_LABEL[j.method] ?? j.method },
    {
      title: "狀態",
      dataIndex: "display_status",
      width: 140,
      render: (_, j) => <Tag color={JobStatusColor[j.display_status] ?? "default"}>{j.display_status}</Tag>,
    },
    { title: "結束代碼", dataIndex: "exit_code", width: 90, render: (_, j) => (j.exit_code ?? "—") },
    {
      title: "佇列時間",
      dataIndex: "queued_at",
      width: 180,
      render: (_, j) => new Date(j.queued_at).toLocaleString(),
    },
    {
      title: "耗時",
      width: 110,
      render: (_, j) =>
        j.started_at && j.finished_at
          ? humanDuration((new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000)
          : "—",
    },
    {
      title: "操作",
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
            日誌
          </Button>
          {canEdit && isActive(j) && !j.cancel_requested_at && (
            <Popconfirm title="取消此任務?" okText="確定取消" onConfirm={() => cancelJob.mutate(j.id)}>
              <Button danger size="small">取消</Button>
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
          aria-label="類型"
          style={{ width: 120 }}
          value={type}
          onChange={(v) => setType(v)}
          disabled={!canEdit}
          options={TYPE_OPTIONS}
        />
        <Select
          aria-label="方法"
          style={{ width: 120 }}
          value={method}
          onChange={(v) => setMethod(v)}
          disabled={!canEdit}
          options={METHOD_OPTIONS}
        />
        <Button type="primary" disabled={!canEdit} loading={startJob.isPending} onClick={confirmLaunch}>
          開始索引
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
