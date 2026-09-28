import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Button, Modal, Space, Spin, Upload, message } from "antd";
import type { UploadProps } from "antd";
import { apiJson, sendOk } from "../api/client";
import { jobsPreflight, projectFiles, projectTags } from "../api/queries";
import { isFrozen } from "./project/frozen";
import type { BulkDeleteResult, Project, UploadedFile } from "../api/types";
import FilePreviewDrawer from "./files/FilePreviewDrawer";
import FilesToolbar from "./files/FilesToolbar";
import FilesTable from "./files/FilesTable";
import { humanBytes, isIndexState } from "./files/indexState";
import type { IndexState } from "./files/indexState";

// Mirrors the backend whitelist (services/files.py ALLOWED_EXTENSIONS):
// text → txt/md, csv → csv, json → json.
const ACCEPT: Record<"text" | "csv" | "json", string> = {
  text: ".txt,.md",
  csv: ".csv",
  json: ".json",
};

// ingest_check reasons that carry an explanatory sentence (spec §6.3). Any
// other non-available value still shows the banner, just without a reason.
const INGEST_REASONS = [
  "unavailable_no_baseline", "unavailable_not_indexed", "unavailable_title_column",
] as const;

export default function FilesPanel({ projectId, inputFileType, canEdit }: {
  projectId: string;
  // ProjectOut.input_file_type is a plain string; the backend Literal on the
  // create body keeps the runtime values inside ACCEPT's keys.
  inputFileType: Project["input_file_type"];
  canEdit: boolean;
}) {
  const accept = ACCEPT[inputFileType as keyof typeof ACCEPT];
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [previewName, setPreviewName] = useState<string | null>(null);
  const [searchParams, setSearchParams] = useSearchParams();

  const files = useQuery(projectFiles(projectId));
  const invalidateFiles = () => qc.invalidateQueries({ queryKey: projectFiles(projectId).queryKey });

  // Tag catalog for the toolbar's filter (GET /tags, Task 6); quiet on
  // failure (see projectTags).
  const tags = useQuery(projectTags(projectId));

  const deleteFile = useMutation({
    mutationFn: (name: string) => sendOk(
      `/api/projects/${projectId}/files/${encodeURIComponent(name)}`, "files.deleteFailed",
      { method: "DELETE" },
    ),
    onSuccess: () => {
      message.success(t("files.deleted"));
      void invalidateFiles();
    },
  });

  // Frozen = an index/update job holds the project (spec 5.2b): the backend
  // refuses uploads/deletes/bulk deletes with 409 while it runs. The panel
  // disables the affordances and says why, rather than letting the user
  // discover the 409. The preflight every pane shares; quiet on failure
  // (a missing preflight costs the lock, not the panel).
  const preflight = useQuery(jobsPreflight(projectId));
  const frozen = isFrozen(preflight.data);

  const bulkDelete = useMutation({
    mutationFn: (names: string[]) => apiJson<BulkDeleteResult>(
      `/api/projects/${projectId}/files:bulk-delete`, "files.bulkDeleteFailed",
      { method: "POST", body: JSON.stringify({ names }) },
    ),
    onSuccess: (result) => {
      // The server's count, not the selection's: a file whose unlink failed
      // stays, and says so by name.
      message.success(t("files.bulkDeleteDone", { n: result.deleted }));
      if (result.failed.length > 0) {
        message.warning(
          t("files.bulkDeletePartial", { n: result.failed.length, names: result.failed.join(", ") }),
        );
      }
      setSelected([]);
      void invalidateFiles();
    },
  });

  // Deleting N documents is not the same act as deleting one: the confirm
  // names the count and the total size before anything is unlinked.
  const confirmBulkDelete = () => {
    const rows = all.filter((f) => selected.includes(f.name));
    const bytes = rows.reduce((n, f) => n + (f.size ?? 0), 0);
    Modal.confirm({
      title: t("files.bulkDeleteTitle", { n: rows.length, size: humanBytes(bytes) }),
      okText: t("common.delete"),
      okButtonProps: { danger: true },
      onOk: () => bulkDelete.mutate(rows.map((f) => f.name)),
    });
  };

  // customRequest keeps the multipart POST inside api() so the auth header
  // and 401-retry apply; the browser sets the multipart boundary itself.
  const customRequest: UploadProps["customRequest"] = async ({ file, onSuccess, onError }) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const out = await apiJson<UploadedFile>(
        `/api/projects/${projectId}/files`, "files.uploadFailed", { method: "POST", body: fd },
      );
      onSuccess?.(out, file);
      message.success(t("files.uploaded", { name: (file as File).name }));
      void invalidateFiles();
    } catch (e) {
      // HTTP errors arrive localized; api() rethrows network-level
      // failures, which surface the same way
      const error = e instanceof Error ? e : new Error(t("files.uploadNetworkFailed"));
      onError?.(error);
      message.error(error.message);
    }
  };

  // The state filter lives in ?state= (comma-separated): the slice ③
  // overview's action cards land on it, and a filter that survives reload
  // and sharing is worth a query param. Unknown tokens are dropped rather
  // than matching nothing.
  const stateFilter = (searchParams.get("state") ?? "").split(",").filter(isIndexState);
  const onState = (v: IndexState[]) => {
    setSearchParams((prev) => {
      const next = new URLSearchParams(prev);
      if (v.length > 0) next.set("state", v.join(","));
      else next.delete("state");
      return next;
    }, { replace: true });
  };

  // Filtering is client-side: hundreds of rows do not need server paging,
  // and a round trip per keystroke would be worse than the render it saves.
  const all = files.data?.files ?? [];
  const q = search.trim().toLowerCase();
  const visible = all.filter((f) =>
    (q === "" || f.name.toLowerCase().includes(q))
    && (selectedTags.length === 0 || selectedTags.every((tag) => f.tags.includes(tag)))
    && (stateFilter.length === 0 || stateFilter.some((s) => s === f.index_state)));

  // "Not yet indexed" = new + modified (spec §9.1): the count that only an
  // index/update run can take to zero.
  const pending = all.filter((f) => f.index_state === "new" || f.index_state === "modified").length;
  const ingestReason = INGEST_REASONS.find((r) => r === files.data?.ingest_check);

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {canEdit && (
        <Upload.Dragger
          accept={accept}
          customRequest={customRequest}
          showUploadList={false}
          multiple
          disabled={frozen}
        >
          <p className="ant-upload-text">{t("files.uploadHint")}</p>
          <p className="ant-upload-hint">{t("files.acceptHint", { accept })}</p>
        </Upload.Dragger>
      )}
      {frozen && (
        <Alert type="warning" showIcon message={t("files.frozenNotice")} />
      )}
      <FilesToolbar
        search={search}
        onSearch={setSearch}
        tags={tags.data?.tags ?? []}
        selectedTags={selectedTags}
        onTags={setSelectedTags}
        state={stateFilter}
        onState={onState}
        usageBytes={files.data?.usage_bytes ?? 0}
        quotaBytes={files.data?.quota_bytes ?? 0}
      />
      {/* The jobs pane is one sidebar entry away: its routed URL is the
          deep link (slice ③), same entry-point pattern as ?state=. */}
      {pending > 0 && (
        <Alert
          type="info"
          showIcon
          message={t("files.notIndexedBar", { n: pending })}
          action={<Link to={`/projects/${projectId}/jobs`}>{t("files.goToJobs")}</Link>}
        />
      )}
      {files.data && files.data.ingest_check !== "available" && (
        <Alert
          type="warning"
          showIcon
          message={t("files.ingestCheck.off")}
          description={ingestReason ? t(`files.ingestCheck.${ingestReason}`) : undefined}
        />
      )}
      {canEdit && (
        <Space>
          <Button
            danger
            disabled={frozen || selected.length === 0}
            onClick={confirmBulkDelete}
          >
            {t("files.bulkDelete")}
          </Button>
        </Space>
      )}
      <Spin spinning={files.isFetching}>
        <FilesTable
          files={visible}
          canEdit={canEdit}
          frozen={frozen}
          selected={selected}
          onSelect={setSelected}
          onDelete={(name) => deleteFile.mutate(name)}
          onPreview={setPreviewName}
        />
      </Spin>
      <FilePreviewDrawer
        projectId={projectId}
        name={previewName}
        onClose={() => setPreviewName(null)}
      />
    </Space>
  );
}
