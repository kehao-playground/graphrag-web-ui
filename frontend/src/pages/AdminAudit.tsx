import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Card, Empty, Input, Space, Table, Tag, Typography, message } from "antd";
import type { TableProps } from "antd";
import { api, detailOf } from "../api/client";
import type { components } from "../api/types.generated";

type AuditEntry = components["schemas"]["AuditEntryOut"];
type AuditPage = components["schemas"]["AuditPageOut"];

const PAGE_SIZE = 50;

// The action namespace ("user", "file", "env", …) is the useful colour: it
// groups a long list far better than the 20-odd individual verbs would, and
// it stays correct when a new action is added.
const NAMESPACE_COLORS: Record<string, string> = {
  user: "blue",
  role: "purple",
  project: "geekblue",
  file: "green",
  env: "orange",
  settings: "gold",
  job: "cyan",
};

export default function AdminAudit() {
  const { t } = useTranslation();
  const [page, setPage] = useState(1);
  const [action, setAction] = useState("");
  const [targetType, setTargetType] = useState("");

  // Changing a filter shortens the result set, so a stale page number would
  // land the reader on an empty page. Reset from the event that caused it
  // rather than from an effect watching the filters.
  const applyAction = (v: string) => {
    setAction(v);
    setPage(1);
  };
  const applyTargetType = (v: string) => {
    setTargetType(v);
    setPage(1);
  };

  const query = useQuery({
    queryKey: ["admin", "audit", page, action, targetType],
    queryFn: async () => {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String((page - 1) * PAGE_SIZE),
      });
      if (action) params.set("action", action);
      if (targetType) params.set("target_type", targetType);
      const r = await api(`/api/admin/audit?${params}`);
      if (!r.ok) throw new Error(await detailOf(r, "adminAudit.loadFailed"));
      return (await r.json()) as AuditPage;
    },
  });

  useEffect(() => {
    if (query.error) message.error(query.error.message);
  }, [query.error]);

  const columns: TableProps<AuditEntry>["columns"] = [
    {
      title: t("adminAudit.when"),
      dataIndex: "created_at",
      width: 200,
      render: (v: string) => new Date(v).toLocaleString(),
    },
    {
      title: t("adminAudit.actor"),
      dataIndex: "actor_email",
      width: 220,
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
      width: 200,
      render: (v: string) => <Tag color={NAMESPACE_COLORS[v.split(".")[0]]}>{v}</Tag>,
    },
    {
      title: t("adminAudit.target"),
      width: 260,
      render: (_: unknown, row: AuditEntry) => (
        <Typography.Text code copyable={{ text: row.target_id }}>
          {row.target_type}:{row.target_id.slice(0, 8)}
        </Typography.Text>
      ),
    },
    {
      title: t("adminAudit.details"),
      dataIndex: "payload",
      render: (payload: Record<string, unknown> | null) =>
        payload ? (
          <Typography.Text style={{ fontSize: 12 }}>{JSON.stringify(payload)}</Typography.Text>
        ) : null,
    },
  ];

  return (
    <Card title={t("adminAudit.title")}>
      <Space orientation="vertical" size="middle" style={{ width: "100%" }}>
        <Alert type="info" showIcon message={t("adminAudit.retention")} />
        <Space wrap>
          <Input.Search
            allowClear
            style={{ width: 240 }}
            placeholder={t("adminAudit.filterAction")}
            aria-label={t("adminAudit.filterAction")}
            onSearch={applyAction}
          />
          <Input.Search
            allowClear
            style={{ width: 240 }}
            placeholder={t("adminAudit.filterTargetType")}
            aria-label={t("adminAudit.filterTargetType")}
            onSearch={applyTargetType}
          />
        </Space>
        <Table
          rowKey="id"
          size="small"
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
      </Space>
    </Card>
  );
}
