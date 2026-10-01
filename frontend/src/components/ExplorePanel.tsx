import { lazy, Suspense, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import type { ParseKeys } from "i18next";
import {
  Alert, Descriptions, Drawer, Input, InputNumber, Segmented, Select, Space, Spin, Table, Typography,
} from "antd";
import type { TableProps } from "antd";
import { artifactDetail, artifactList, artifactTables } from "../api/queries";
import { i18n } from "../i18n";
import { formatDateTime } from "../i18n/format";
import ArtifactQueryError from "./ArtifactQueryError";
import ErrorBoundary from "./ErrorBoundary";
import type { ArtifactTableName } from "../api/types";

// the graph stack (sigma + graphology, ~204 kB chunk / ~51 kB gzip):
// lazy-load it so it lands in its own chunk, fetched the first time graph
// mode is used. React caches a rejected lazy() for good, so the error
// boundary's Retry swaps in a fresh one (see graphView state below).
const loadGraphView = () => lazy(() => import("./GraphView"));

type Row = Record<string, unknown>;
type Mode = "graph" | "table";

// Localized label for any column the detail drawer can show (get_row returns
// SELECT *, a superset of the list projections). Dynamic template key — the
// ParseKeys cast is needed because a typed union cannot absorb `${string}`.
// A column outside the catalog (another graphrag version) shows its own name.
const columnLabel = (k: string) => i18n.t(`explore.columns.${k}` as ParseKeys, { defaultValue: k });

// Localized table labels, in the registry's order. The list columns and
// filter flags come from the backend registry (GET /api/artifact-tables,
// R1-114) rather than a client-side mirror.
const TABLE_LABELS: Record<ArtifactTableName, ParseKeys> = {
  entities: "explore.tableEntities",
  relationships: "explore.tableRelationships",
  communities: "explore.tableCommunities",
  community_reports: "explore.tableCommunityReports",
  text_units: "explore.tableTextUnits",
  documents: "explore.tableDocuments",
};

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

// graphrag's timestamp columns ("2026-09-21 00:18:35 +0000") read in the
// active locale like every other date (R4-33).
const DATE_COLUMNS = new Set(["creation_date"]);

function renderCell(k: string, v: unknown) {
  if (DATE_COLUMNS.has(k) && typeof v === "string") return formatDateTime(v, i18n.language);
  return isHashId(k) ? renderHashIds(v) : renderValue(v);
}

// Hash ids (the internal `id`, the `*_ids` lists) mean nothing to a reader:
// they show as a count, or a "show" toggle, that expands to the raw values.
const isHashId = (k: string) => k === "id" || k.endsWith("_ids");

function renderHashIds(v: unknown) {
  if (v === null) return renderValue(v);
  return (
    <details>
      <summary style={{ cursor: "pointer" }}>
        {Array.isArray(v) ? i18n.t("explore.idCount", { count: v.length }) : i18n.t("explore.showId")}
      </summary>
      {renderValue(v)}
    </details>
  );
}

export default function ExplorePanel({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const tables = useQuery(artifactTables());
  const TABLE_OPTIONS = (tables.data?.tables ?? []).map(({ name }) => ({
    label: t(TABLE_LABELS[name]),
    value: name,
  }));
  // A citation links here as ?table=<name>&row=<human_readable_id>: that
  // table, with the row's detail open. Read once; the drawer's close drops it.
  const [params, setParams] = useSearchParams();
  const linkedTable = params.get("table");
  const linked = linkedTable !== null && Object.hasOwn(TABLE_LABELS, linkedTable) ? linkedTable as ArtifactTableName : null;
  const linkedRow = Number(params.get("row") ?? "");
  const [mode, setMode] = useState<Mode>("table");
  const [GraphView, setGraphView] = useState(loadGraphView);
  const [table, setTable] = useState<ArtifactTableName>(linked ?? "entities");
  const [offset, setOffset] = useState(0);
  const [limit, setLimit] = useState(50);
  const [q, setQ] = useState("");
  // Tags Select constrained to one value: the backend type filter is a single
  // equality (domain keyword_fields flag), not a set membership test.
  const [typeTags, setTypeTags] = useState<string[]>([]);
  const [community, setCommunity] = useState<number | null>(null);
  const [hrid, setHrid] = useState<number | null>(
    linked && params.get("row") !== null && Number.isInteger(linkedRow) ? linkedRow : null,
  );
  const closeDetail = () => {
    setHrid(null);
    if (params.has("row")) {
      setParams((p) => {
        p.delete("row");
        return p;
      }, { replace: true });
    }
  };

  // Undefined until the registry has loaded: no list request before then.
  const meta = tables.data?.tables.find((m) => m.name === table);

  // The key is the request actually sent: filters the table does not
  // support never reach it (or the cache key). Errors render in place
  // (ArtifactQueryError, the drawer's Alert), so no toast on top.
  const list = useQuery({
    ...artifactList(projectId, table, {
      limit,
      offset,
      q: q || undefined,
      type: meta?.type_filter ? typeTags[0] : undefined,
      community: meta?.community_filter && community !== null ? community : undefined,
    }),
    enabled: mode === "table" && meta !== undefined,
    meta: { silent: true },
  });

  const detail = useQuery({
    ...artifactDetail(projectId, table, hrid ?? -1),
    enabled: hrid !== null,
    meta: { silent: true },
  });

  // Any filter/table change restarts at page 1 (offset 0).
  const resetPage = () => setOffset(0);

  // A graph node click opens that entity's detail — the same drawer the
  // table uses, so the table behind it switches to entities as well.
  const openEntity = (id: number) => {
    if (table !== "entities") {
      setTable("entities");
      resetPage();
    }
    setHrid(id);
  };

  const columns: TableProps<Row>["columns"] = (meta?.columns ?? []).map((c) => ({
    title: columnLabel(c),
    dataIndex: c,
    ellipsis: true,
    ...(DATE_COLUMNS.has(c) && { render: (v: unknown) => (typeof v === "string" ? formatDateTime(v, i18n.language) : String(v ?? "")) }),
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
        <ErrorBoundary onReset={() => setGraphView(() => loadGraphView())}>
          <Suspense fallback={<Spin style={{ display: "block", marginTop: 64 }} />}>
            <GraphView projectId={projectId} onOpenNode={openEntity} />
          </Suspense>
        </ErrorBoundary>
      ) : (
        <>
          <Space wrap>
            <Select
              aria-label={t("explore.modeTable")}
              style={{ width: 140 }}
              value={table}
              options={TABLE_OPTIONS}
              onChange={(name) => { setTable(name); setHrid(null); resetPage(); }}
            />
            <Input.Search
              aria-label={t("explore.search")}
              placeholder={t("explore.searchPlaceholder")}
              style={{ width: 220 }}
              allowClear
              onSearch={(v) => { setQ(v.trim()); resetPage(); }}
            />
            {meta?.type_filter && (
              <Select
                aria-label={t("explore.columns.type")}
                mode="tags"
                maxCount={1}
                placeholder={t("explore.columns.type")}
                style={{ minWidth: 160 }}
                value={typeTags}
                onChange={(tags) => { setTypeTags(tags); resetPage(); }}
              />
            )}
            {meta?.community_filter && (
              <InputNumber
                aria-label={t("explore.columns.community")}
                placeholder={t("explore.columns.community")}
                min={0}
                value={community}
                onChange={(v) => { setCommunity(v); resetPage(); }}
              />
            )}
          </Space>
          {list.error ? <ArtifactQueryError error={list.error} projectId={projectId} /> : (
          <Table
            rowKey="human_readable_id"
            size="small"
            loading={tables.isPending || list.isFetching}
            dataSource={list.data?.rows ?? []}
            columns={columns}
            pagination={{
              current: Math.floor(offset / limit) + 1,
              pageSize: limit,
              total: list.data?.total ?? 0,
              showSizeChanger: true,
              pageSizeOptions: [10, 20, 50, 100],
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
          )}
        </>
      )}
      <Drawer
        title={t(TABLE_LABELS[table])}
        size="large"
        open={hrid !== null}
        onClose={closeDetail}
      >
        {detail.error ? (
          <Alert type="error" showIcon message={detail.error.message} />
        ) : detail.data ? (
          <Descriptions
            column={1}
            size="small"
            bordered
            // zh labels wrapped per character in the drawer's width (R4-32).
            styles={{ label: { width: 120, whiteSpace: "nowrap" } }}
          >
            {Object.entries(detail.data.row).map(([k, v]) => (
              <Descriptions.Item key={k} label={columnLabel(k)}>{renderCell(k, v)}</Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Spin style={{ display: "block", marginTop: 64 }} />
        )}
      </Drawer>
    </Space>
  );
}
