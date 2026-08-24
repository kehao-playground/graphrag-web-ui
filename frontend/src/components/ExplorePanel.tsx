import { lazy, Suspense, useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import type { ParseKeys } from "i18next";
import {
  Alert, Descriptions, Drawer, Input, InputNumber, Segmented, Select, Space, Spin, Table, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { fetchArtifactDetail, fetchArtifacts } from "../api/client";
import { i18n } from "../i18n";
import type { ArtifactTableName } from "../api/types";

// the graph stack (sigma + graphology, ~204 kB chunk / ~51 kB gzip):
// lazy-load it so it lands in its own chunk, fetched the first time graph
// mode is used.
const GraphView = lazy(() => import("./GraphView"));

type Row = Record<string, unknown>;
type Mode = "graph" | "table";

// Localized label for any column the detail drawer can show (get_row returns
// SELECT *, a superset of the list projections). Dynamic template key — the
// ParseKeys cast follows client.ts (a typed union can't absorb `${string}`).
const columnLabel = (k: string) => i18n.t(`explore.columns.${k}` as ParseKeys);

// Mirror of the backend domain registry (Task 1): localized table label, the
// list_columns projection and the filter flags the parquet schema supports.
interface TableMeta {
  label: string;
  columns: string[];
  typeFilter: boolean;
  communityFilter: boolean;
}

// Detail rows mix ids, long prose and list/object columns: prose stays
// wrap-able, structured values are serialized for readability.
function renderValue(v: unknown) {
  if (v === null) return i18n.t("common.notApplicable");
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v === "string") {
    return <Typography.Paragraph style={{ marginBottom: 0 }} copyable>{v}</Typography.Paragraph>;
  }
  return <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(v, null, 2)}</pre>;
}

export default function ExplorePanel({ projectId, canUse }: { projectId: string; canUse: boolean }) {
  const { t } = useTranslation();
  // Labels localize per render; the projection/filter flags are static.
  const TABLE_META: Record<ArtifactTableName, TableMeta> = {
    entities: {
      label: t("explore.tableEntities"),
      columns: ["human_readable_id", "title", "type", "frequency", "degree"],
      typeFilter: true,
      communityFilter: true,
    },
    relationships: {
      label: t("explore.tableRelationships"),
      columns: ["human_readable_id", "source", "target", "weight", "combined_degree"],
      typeFilter: false,
      communityFilter: false,
    },
    communities: {
      label: t("explore.tableCommunities"),
      columns: ["human_readable_id", "community", "level", "parent", "size", "title"],
      typeFilter: false,
      communityFilter: true,
    },
    community_reports: {
      label: t("explore.tableCommunityReports"),
      columns: ["human_readable_id", "community", "level", "rank", "title"],
      typeFilter: false,
      communityFilter: true,
    },
    text_units: {
      label: t("explore.tableTextUnits"),
      columns: ["human_readable_id", "n_tokens", "document_id"],
      typeFilter: false,
      communityFilter: false,
    },
    documents: {
      label: t("explore.tableDocuments"),
      columns: ["human_readable_id", "title", "creation_date"],
      typeFilter: false,
      communityFilter: false,
    },
  };
  const TABLE_OPTIONS = (Object.keys(TABLE_META) as ArtifactTableName[]).map((name) => ({
    label: TABLE_META[name].label,
    value: name,
  }));
  const [mode, setMode] = useState<Mode>("table");
  const [table, setTable] = useState<ArtifactTableName>("entities");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [q, setQ] = useState("");
  // Tags Select constrained to one value: the backend type filter is a single
  // equality (domain keyword_fields flag), not a set membership test.
  const [typeTags, setTypeTags] = useState<string[]>([]);
  const [community, setCommunity] = useState<number | null>(null);
  const [hrid, setHrid] = useState<number | null>(null);

  const meta = TABLE_META[table];

  const list = useQuery({
    queryKey: ["projects", projectId, "artifacts", table, { limit, offset, q, type: typeTags[0], community }],
    queryFn: () => fetchArtifacts(projectId, table, {
      limit,
      offset,
      q: q || undefined,
      type: meta.typeFilter ? typeTags[0] : undefined,
      community: meta.communityFilter && community !== null ? community : undefined,
    }),
    enabled: canUse && mode === "table",
    placeholderData: keepPreviousData,
    retry: false,
  });

  const detail = useQuery({
    queryKey: ["projects", projectId, "artifacts", table, "detail", hrid],
    queryFn: () => fetchArtifactDetail(projectId, table, hrid as number),
    enabled: canUse && hrid !== null,
    retry: false,
  });

  useEffect(() => {
    if (list.error) message.error(list.error.message);
  }, [list.error]);
  useEffect(() => {
    if (detail.error) message.error(detail.error.message);
  }, [detail.error]);

  // Any filter/table change restarts at page 1 (offset 0).
  const resetPage = () => setOffset(0);

  const columns: TableProps<Row>["columns"] = meta.columns.map((c) => ({
    title: columnLabel(c),
    dataIndex: c,
    ellipsis: true,
  }));

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {/* Shown in both modes while an index job makes results incomplete —
          each mode surfaces it from its own query (GraphView in graph mode). */}
      {mode === "table" && list.data?.stale && <Alert type="warning" showIcon message={t("explore.staleWarning")} />}
      <Segmented
        value={mode}
        onChange={(v) => setMode(v as Mode)}
        options={[{ label: t("explore.modeGraph"), value: "graph" }, { label: t("explore.modeTable"), value: "table" }]}
      />
      {mode === "graph" ? (
        <Suspense fallback={<Spin style={{ display: "block", marginTop: 64 }} />}>
          <GraphView projectId={projectId} canUse={canUse} />
        </Suspense>
      ) : (
        <>
          <Space wrap>
            <Select
              aria-label={t("explore.modeTable")}
              style={{ width: 140 }}
              value={table}
              disabled={!canUse}
              options={TABLE_OPTIONS}
              onChange={(name) => { setTable(name); setHrid(null); resetPage(); }}
            />
            <Input.Search
              aria-label={t("explore.search")}
              placeholder={t("explore.searchPlaceholder")}
              style={{ width: 220 }}
              disabled={!canUse}
              allowClear
              onSearch={(v) => { setQ(v.trim()); resetPage(); }}
            />
            {meta.typeFilter && (
              <Select
                aria-label={t("explore.columns.type")}
                mode="tags"
                maxCount={1}
                placeholder={t("explore.columns.type")}
                style={{ minWidth: 160 }}
                disabled={!canUse}
                value={typeTags}
                onChange={(tags) => { setTypeTags(tags); resetPage(); }}
              />
            )}
            {meta.communityFilter && (
              <InputNumber
                aria-label={t("explore.columns.community")}
                placeholder={t("explore.columns.community")}
                min={0}
                disabled={!canUse}
                value={community}
                onChange={(v) => { setCommunity(v); resetPage(); }}
              />
            )}
          </Space>
          <Table
            rowKey="human_readable_id"
            size="small"
            loading={list.isFetching}
            dataSource={list.data?.rows ?? []}
            columns={columns}
            pagination={{
              current: Math.floor(offset / limit) + 1,
              pageSize: limit,
              total: list.data?.total ?? 0,
              showSizeChanger: true,
              pageSizeOptions: [1, 10, 50, 100],
              onChange: (page, pageSize) => {
                setOffset((page - 1) * pageSize);
                setLimit(pageSize);
              },
            }}
            onRow={(record) => ({
              onClick: () => setHrid(record.human_readable_id as number),
              style: { cursor: "pointer" },
            })}
          />
        </>
      )}
      <Drawer
        title={meta.label}
        size="large"
        open={hrid !== null}
        onClose={() => setHrid(null)}
      >
        {detail.data ? (
          <Descriptions column={1} size="small" bordered>
            {Object.entries(detail.data.row).map(([k, v]) => (
              <Descriptions.Item key={k} label={columnLabel(k)}>{renderValue(v)}</Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Spin style={{ display: "block", marginTop: 64 }} />
        )}
      </Drawer>
    </Space>
  );
}
