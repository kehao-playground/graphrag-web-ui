import { lazy, Suspense, useEffect, useState } from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import {
  Alert, Descriptions, Drawer, Input, InputNumber, Segmented, Select, Space, Spin, Table, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { fetchArtifactDetail, fetchArtifacts } from "../api/client";
import type { ArtifactTableName } from "../api/types";

// the graph stack (sigma + graphology, ~204 kB chunk / ~51 kB gzip):
// lazy-load it so it lands in its own chunk, fetched the first time graph
// mode is used.
const GraphView = lazy(() => import("./GraphView"));

type Row = Record<string, unknown>;
type Mode = "graph" | "table";

// zh-TW labels for every column the detail drawer can show (get_row returns
// SELECT *, a superset of the list projections).
const COLUMN_LABEL: Record<string, string> = {
  id: "內部 ID", human_readable_id: "ID", title: "標題", type: "類型",
  frequency: "頻率", degree: "度", description: "描述", text_unit_ids: "文本單元 ID",
  source: "來源", target: "目標", weight: "權重", combined_degree: "綜合度",
  community: "社群", level: "層級", parent: "父層", children: "子層", size: "大小",
  entity_ids: "實體 ID", relationship_ids: "關係 ID", period: "期間",
  rank: "排名", rank_score: "排名分數", summary: "摘要", full_content: "完整內容",
  findings: "發現", created_at: "建立時間", updated_at: "更新時間",
  n_tokens: "Token 數", document_id: "文件 ID", text: "文本",
  raw_data: "原始內容", creation_date: "建立日期",
};

// Mirror of the backend domain registry (Task 1): zh-TW table label, the
// list_columns projection and the filter flags the parquet schema supports.
interface TableMeta {
  label: string;
  columns: string[];
  typeFilter: boolean;
  communityFilter: boolean;
}
const TABLE_META: Record<ArtifactTableName, TableMeta> = {
  entities: {
    label: "實體",
    columns: ["human_readable_id", "title", "type", "frequency", "degree"],
    typeFilter: true,
    communityFilter: true,
  },
  relationships: {
    label: "關係",
    columns: ["human_readable_id", "source", "target", "weight", "combined_degree"],
    typeFilter: false,
    communityFilter: false,
  },
  communities: {
    label: "社群",
    columns: ["human_readable_id", "community", "level", "parent", "size", "title"],
    typeFilter: false,
    communityFilter: true,
  },
  community_reports: {
    label: "社群報告",
    columns: ["human_readable_id", "community", "level", "rank", "title"],
    typeFilter: false,
    communityFilter: true,
  },
  text_units: {
    label: "文本單元",
    columns: ["human_readable_id", "n_tokens", "document_id"],
    typeFilter: false,
    communityFilter: false,
  },
  documents: {
    label: "文件",
    columns: ["human_readable_id", "title", "creation_date"],
    typeFilter: false,
    communityFilter: false,
  },
};
const TABLE_OPTIONS = (Object.keys(TABLE_META) as ArtifactTableName[]).map((t) => ({
  label: TABLE_META[t].label,
  value: t,
}));

// Detail rows mix ids, long prose and list/object columns: prose stays
// wrap-able, structured values are serialized for readability.
function renderValue(v: unknown) {
  if (v === null) return "—";
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (typeof v === "string") {
    return <Typography.Paragraph style={{ marginBottom: 0 }} copyable>{v}</Typography.Paragraph>;
  }
  return <pre style={{ margin: 0, whiteSpace: "pre-wrap", fontSize: 12 }}>{JSON.stringify(v, null, 2)}</pre>;
}

export default function ExplorePanel({ projectId, canUse }: { projectId: string; canUse: boolean }) {
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
    title: COLUMN_LABEL[c] ?? c,
    dataIndex: c,
    ellipsis: true,
  }));

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {/* Shown in both modes while an index job makes results incomplete —
          each mode surfaces it from its own query (GraphView in graph mode). */}
      {mode === "table" && list.data?.stale && <Alert type="warning" showIcon message="索引進行中,結果可能不完整" />}
      <Segmented
        value={mode}
        onChange={(v) => setMode(v as Mode)}
        options={[{ label: "圖譜", value: "graph" }, { label: "資料表", value: "table" }]}
      />
      {mode === "graph" ? (
        <Suspense fallback={<Spin style={{ display: "block", marginTop: 64 }} />}>
          <GraphView projectId={projectId} canUse={canUse} />
        </Suspense>
      ) : (
        <>
          <Space wrap>
            <Select
              aria-label="資料表"
              style={{ width: 140 }}
              value={table}
              disabled={!canUse}
              options={TABLE_OPTIONS}
              onChange={(t) => { setTable(t); setHrid(null); resetPage(); }}
            />
            <Input.Search
              aria-label="搜尋"
              placeholder="搜尋關鍵字"
              style={{ width: 220 }}
              disabled={!canUse}
              allowClear
              onSearch={(v) => { setQ(v.trim()); resetPage(); }}
            />
            {meta.typeFilter && (
              <Select
                aria-label="類型"
                mode="tags"
                maxCount={1}
                placeholder="類型"
                style={{ minWidth: 160 }}
                disabled={!canUse}
                value={typeTags}
                onChange={(tags) => { setTypeTags(tags); resetPage(); }}
              />
            )}
            {meta.communityFilter && (
              <InputNumber
                aria-label="社群"
                placeholder="社群"
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
              <Descriptions.Item key={k} label={COLUMN_LABEL[k] ?? k}>{renderValue(v)}</Descriptions.Item>
            ))}
          </Descriptions>
        ) : (
          <Spin style={{ display: "block", marginTop: 64 }} />
        )}
      </Drawer>
    </Space>
  );
}
