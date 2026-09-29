import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Card, Empty, Select, Space, Table, Tag, Typography } from "antd";
import type { TableProps } from "antd";
import { adminAudit, projectsList, usersBrief } from "../api/queries";
import type { AuditEntry } from "../api/types";
import { AUDIT_ACTIONS, auditActionLabel } from "../components/labels";

const PAGE_SIZE = 50;

// The action namespace ("user", "file", "env", …) is the useful colour: it
// groups a long list far better than the 20-odd individual verbs would, and
// it stays correct when a new action is added.
const NAMESPACE_COLORS: Record<string, string> = {
  user: "blue",
  role: "purple",
  project: "geekblue",
  member: "geekblue",
  file: "green",
  env: "orange",
  settings: "gold",
  job: "cyan",
};

// One payload value on one line: scalars as written, anything nested as
// compact JSON (rare — payloads are flat by convention).
const valueText = (v: unknown): string =>
  v === null || v === undefined ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v);

export default function AdminAudit() {
  const { t, i18n } = useTranslation();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");

  // Changing a filter shortens the result set, so a stale page number would
  // land the reader on an empty page. Reset from the event that caused it
  // rather than from an effect watching the filters.
  const applyAction = (v: string | undefined) => {
    setAction(v ?? "");
    setPage(1);
  };
  const applyTargetType = (v: string | undefined) => {
    setTargetType(v ?? "");
    setPage(1);
  };

  const query = useQuery(adminAudit({
    limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, action, targetType,
  }));

  // Targets are ids; name the ones the reader can already see (R4-17). An
  // id neither list knows (a deleted project, say) keeps its short form.
  const projects = useQuery(projectsList());
  const users = useQuery(usersBrief());
  const targetName = useMemo(() => {
    const byId = new Map<string, string>();
    for (const p of projects.data ?? []) byId.set(`project:${p.id}`, p.name);
    for (const u of users.data ?? []) byId.set(`user:${u.id}`, u.email);
    return (row: AuditEntry) => byId.get(`${row.target_type}:${row.target_id}`);
  }, [projects.data, users.data]);

  const targetTypeLabel = (v: string) =>
    v === "project" ? t("adminAudit.targetProject")
      : v === "user" ? t("adminAudit.targetUser")
      : v === "role" ? t("adminAudit.targetRole")
      : v;

  const columns: TableProps<AuditEntry>["columns"] = [
    {
      title: t("adminAudit.when"),
      dataIndex: "created_at",
      width: 180,
      render: (v: string) => new Date(v).toLocaleString(i18n.language),
    },
    {
      title: t("adminAudit.actor"),
      dataIndex: "actor_email",
      width: 200,
      ellipsis: true,
      // Null actor_id means nobody was signed in (bootstrap); a null email
      // with a real id means the user row is gone. Different facts, so they
      // must not render the same.
      render: (email: string | null, row: AuditEntry) =>
        email ?? (
          <Typography.Text type="secondary">
            {row.actor_id ? t("adminAudit.deletedActor") : t("adminAudit.system")}
          </Typography.Text>
        ),
    },
    {
      title: t("adminAudit.action"),
      dataIndex: "action",
      width: 180,
      render: (v: string) => (
        <Tag color={NAMESPACE_COLORS[v.split(".")[0]]} title={v}>{auditActionLabel(v, t)}</Tag>
      ),
    },
    {
      title: t("adminAudit.target"),
      width: 240,
      render: (_: unknown, row: AuditEntry) => {
        const name = targetName(row);
        return (
          <Space size={4} style={{ maxWidth: "100%" }}>
            <Tag>{targetTypeLabel(row.target_type)}</Tag>
            <Typography.Text code={!name} copyable={{ text: row.target_id }}>
              {name ?? row.target_id.slice(0, 8)}
            </Typography.Text>
          </Space>
        );
      },
    },
    {
      // The one column without a width: it takes what the fixed layout
      // leaves, so payloads wrap by word in a wide column (R4-17).
      title: t("adminAudit.details"),
      dataIndex: "payload",
      render: (payload: Record<string, unknown> | null) =>
        payload ? (
          <div style={{ fontSize: 12, overflowWrap: "anywhere" }}>
            {Object.entries(payload).map(([k, v]) => (
              <div key={k}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>{k}: </Typography.Text>
                <span>{valueText(v)}</span>
              </div>
            ))}
          </div>
        ) : null,
    },
  ];

  return (
    <Card title={t("adminAudit.title")}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="info" showIcon message={t("adminAudit.retention")} />
        <Space wrap>
          {/* Filters pick from the catalogs, so nobody has to recall an id. */}
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            style={{ width: 240 }}
            placeholder={t("adminAudit.filterAction")}
            aria-label={t("adminAudit.filterAction")}
            value={action || undefined}
            onChange={applyAction}
            options={AUDIT_ACTIONS.map((a) => ({ value: a, label: auditActionLabel(a, t) }))}
          />
          <Select
            allowClear
            style={{ width: 200 }}
            placeholder={t("adminAudit.filterTargetType")}
            aria-label={t("adminAudit.filterTargetType")}
            value={targetType || undefined}
            onChange={applyTargetType}
            options={["project", "user", "role"].map((v) => ({ value: v, label: targetTypeLabel(v) }))}
          />
        </Space>
        {/* A refused or failed read is not an empty result (R4-17). */}
        {query.error ? (
          <Alert
            type="error"
            showIcon
            message={t("adminAudit.loadFailedTitle")}
            description={query.error.message}
          />
        ) : (
          <Table
            rowKey="id"
            size="small"
            tableLayout="fixed"
            loading={query.isLoading}
            columns={columns}
            dataSource={query.data?.rows ?? []}
            locale={{ emptyText: <Empty description={t("adminAudit.empty")} /> }}
            pagination={{
              current: page,
              pageSize: PAGE_SIZE,
              // Server-side: total comes from the envelope, not the page length.
              total: query.data?.total ?? 0,
              showSizeChanger: false,
              onChange: setPage,
            }}
          />
        )}
      </Space>
    </Card>
  );
}
