import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Popconfirm, Space, Table, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { JobStatusColor } from "../../api/types";
import type { Job } from "../../api/types";
import { i18n } from "../../i18n";
import { formatDateTime } from "../../i18n/format";
import { jobMethodLabel, jobStatusLabel, jobTypeShortLabel } from "../labels";

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

// A job still counts as active while the runner can transition it; the
// cancel affordance keys off this (cancelling = cancel requested, a second
// request would 409).
const isActive = (j: Job) => ["queued", "running"].includes(j.status);

// The project's job history, one server page at a time (R3-10): status
// with workflow progress, duration (elapsed while running) and the log and
// cancel actions; a failed job expands to its error.
export default function JobsTable({
  rows, loading, page, pageSize, total, onPage, canEdit, onLogs, onCancel,
}: {
  rows: Job[];
  loading: boolean;
  page: number;
  pageSize: number;
  total: number;
  onPage: (page: number) => void;
  canEdit: boolean;
  onLogs: (jobId: string) => void;
  onCancel: (jobId: string) => void;
}) {
  const { t } = useTranslation();
  const typeLabel = (v: string) => jobTypeShortLabel(v, t);
  const methodLabel = (v: string) => jobMethodLabel(v, t);

  // Elapsed time on active rows ticks once a second, only while one runs.
  const [now, setNow] = useState(() => Date.now());
  const ticking = rows.some((j) => j.started_at && !j.finished_at);
  useEffect(() => {
    if (!ticking) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [ticking]);

  const columns: TableProps<Job>["columns"] = [
    { title: t("jobs.type"), dataIndex: "type", width: 80, render: (_, j) => typeLabel(j.type) },
    { title: t("jobs.method"), dataIndex: "method", width: 80, render: (_, j) => methodLabel(j.method) },
    {
      title: t("common.status"),
      dataIndex: "display_status",
      width: 140,
      render: (_, j) => (
        <>
          <Tag color={JobStatusColor[j.display_status] ?? "default"}>{jobStatusLabel(j.display_status, t)}</Tag>
          {/* Workflows done, ticked from graphrag's stats.json (R3-36). */}
          {j.status === "running" && j.progress && (
            <div>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {t("jobs.workflowProgress", j.progress)}
              </Typography.Text>
            </div>
          )}
        </>
      ),
    },
    { title: t("jobs.exitCode"), dataIndex: "exit_code", width: 90, render: (_, j) => (j.exit_code ?? t("common.notApplicable")) },
    {
      title: t("jobs.queuedAt"),
      dataIndex: "queued_at",
      width: 190,
      render: (_, j) => formatDateTime(j.queued_at, i18n.language),
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
            onClick={() => onLogs(j.id)}
          >
            {t("jobs.logs")}
          </Button>
          {canEdit && isActive(j) && !j.cancel_requested_at && (
            <Popconfirm title={t("jobs.cancelJobTitle")} okText={t("jobs.confirmCancel")} onConfirm={() => onCancel(j.id)}>
              <Button danger size="small">{t("common.cancel")}</Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ];

  return (
    <Table
      rowKey="id"
      size="small"
      loading={loading}
      dataSource={rows}
      columns={columns}
      pagination={{
        current: page,
        pageSize,
        // Server-side: total comes from the envelope, not the page length.
        total,
        showSizeChanger: false,
        hideOnSinglePage: true,
        onChange: onPage,
      }}
      expandable={{
        rowExpandable: (j) => !!j.error,
        expandedRowRender: (j) => (
          <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12, color: "#cf1322" }}>{j.error}</pre>
        ),
      }}
    />
  );
}
