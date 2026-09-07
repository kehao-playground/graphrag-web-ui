import { useTranslation } from "react-i18next";
import { Button, Popconfirm, Table, Tag, Tooltip } from "antd";
import type { TableProps } from "antd";
import type { FileEntry } from "../../api/types";
import { STATE_COLOR, humanBytes, isIndexState, useStateCopy } from "./indexState";

export default function FilesTable({ files, canEdit, frozen = false, selected, onSelect, onDelete, onPreview }: {
  files: FileEntry[];
  canEdit: boolean;
  // While an index/update job is active the panel locks every mutating
  // action instead of letting the user discover the 409 (spec §9.1). The
  // jobs-derived value is Task 9's wiring; the lock lives here.
  frozen?: boolean;
  // Row selection and the preview drawer are Task 9's; the props are part
  // of this table's contract so that wiring does not churn the call site.
  selected?: string[];
  onSelect?: (names: string[]) => void;
  onDelete: (name: string) => void;
  // Present once the preview drawer exists (Task 9): the row name becomes
  // the entry point. A removed name 404s on preview, so those rows stay
  // plain text.
  onPreview?: (name: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const copy = useStateCopy();

  // Selection exists only for the bulk actions (canEdit). A removed row is
  // inert: the checkbox is hidden (not merely disabled) so the row reads
  // as having nothing to act on, and aria-label carries the filename so
  // row-scoped role queries can address it.
  const rowSelection: TableProps<FileEntry>["rowSelection"] =
    canEdit && onSelect
      ? {
          selectedRowKeys: selected ?? [],
          onChange: (keys) => onSelect(keys as string[]),
          getCheckboxProps: (f: FileEntry) => ({
            disabled: f.index_state === "removed",
            "aria-label": f.name,
            style: f.index_state === "removed" ? { display: "none" } : undefined,
          }),
        }
      : undefined;
  const columns: TableProps<FileEntry>["columns"] = [
    {
      title: t("common.name"),
      dataIndex: "name",
      render: (_, f) => onPreview && f.index_state !== "removed" ? (
        <Button type="link" size="small" style={{ padding: 0 }} onClick={() => onPreview(f.name)}>{f.name}</Button>
      ) : f.name,
    },
    {
      title: t("files.indexState"),
      dataIndex: "index_state",
      width: 110,
      render: (_, f) => {
        // Unknown values have no catalog copy; the raw value is the honest
        // fallback (same policy as JobStatusColor's "default" leg).
        if (!isIndexState(f.index_state)) return <Tag>{f.index_state}</Tag>;
        const c = copy[f.index_state];
        return (
          <Tooltip title={c.sentence}>
            <Tag color={STATE_COLOR[f.index_state]}>{c.label}</Tag>
          </Tooltip>
        );
      },
    },
    {
      title: t("files.tags"),
      dataIndex: "tags",
      render: (_, f) => f.tags.map((tag) => <Tag key={tag}>{tag}</Tag>),
    },
    { title: t("files.size"), dataIndex: "size", width: 110, render: (_, f) => (f.size === null ? "—" : humanBytes(f.size)) },
    // A removed row has no file behind it: the size column carries the em
    // dash, modified stays an empty cell so the gap reads once, not twice.
    { title: t("files.modifiedAt"), dataIndex: "modified_at", width: 190, render: (_, f) => (f.modified_at === null ? null : new Date(f.modified_at).toLocaleString(i18n.language)) },
    ...(canEdit
      ? [{
          title: t("common.actions"),
          width: 90,
          render: (_: unknown, f: FileEntry) => (
            <Popconfirm
              title={t("files.deleteFileTitle", { name: f.name })}
              okText={t("common.delete")}
              okButtonProps={{ danger: true }}
              onConfirm={() => onDelete(f.name)}
            >
              <Button danger size="small" disabled={frozen}>{t("common.delete")}</Button>
            </Popconfirm>
          ),
        }]
      : []),
  ];

  return (
    <Table
      rowKey="name"
      size="small"
      dataSource={files}
      columns={columns}
      rowSelection={rowSelection}
      pagination={false}
    />
  );
}
