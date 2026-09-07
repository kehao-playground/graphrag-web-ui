import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Space, Spin, Upload, message } from "antd";
import type { UploadProps } from "antd";
import { api, detailOf } from "../api/client";
import type { FilesOut, Project, TagCatalog } from "../api/types";
import FilesToolbar from "./files/FilesToolbar";
import FilesTable from "./files/FilesTable";
import { isIndexState } from "./files/indexState";
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
  const [searchParams, setSearchParams] = useSearchParams();

  const files = useQuery({
    queryKey: ["projects", projectId, "files"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/files`);
      if (!r.ok) throw new Error(await detailOf(r, "files.loadFailed"));
      return (await r.json()) as FilesOut;
    },
    retry: false,
  });

  useEffect(() => {
    if (files.error) message.error(files.error.message);
  }, [files.error]);

  // Tag catalog for the toolbar's filter (GET /tags, Task 6). Quiet on
  // failure: a missing catalog costs the filter's options, not the panel.
  const tags = useQuery({
    queryKey: ["projects", projectId, "tags"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/tags`);
      if (!r.ok) throw new Error(await detailOf(r, "files.loadTagsFailed"));
      return (await r.json()) as TagCatalog;
    },
    retry: false,
  });

  const deleteFile = useMutation({
    mutationFn: async (name: string) => {
      const r = await api(`/api/projects/${projectId}/files/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await detailOf(r, "files.deleteFailed"));
    },
    onSuccess: () => {
      message.success(t("files.deleted"));
      qc.invalidateQueries({ queryKey: ["projects", projectId, "files"] });
    },
    onError: (e) => message.error(e.message),
  });

  // customRequest keeps the multipart POST inside api() so the auth header
  // and 401-retry apply; the browser sets the multipart boundary itself.
  const customRequest: UploadProps["customRequest"] = async ({ file, onSuccess, onError }) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await api(`/api/projects/${projectId}/files`, { method: "POST", body: fd });
      if (r.ok) {
        onSuccess?.(await r.json(), file);
        message.success(t("files.uploaded", { name: (file as File).name }));
        qc.invalidateQueries({ queryKey: ["projects", projectId, "files"] });
      } else {
        const error = new Error(await detailOf(r, "files.uploadFailed"));
        onError?.(error);
        message.error(error.message);
      }
    } catch (e) {
      // api() rethrows network-level failures; surface them like HTTP errors
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
        <Upload.Dragger accept={accept} customRequest={customRequest} showUploadList={false} multiple>
          <p className="ant-upload-text">{t("files.uploadHint")}</p>
          <p className="ant-upload-hint">{t("files.acceptHint", { accept })}</p>
        </Upload.Dragger>
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
      {/* The jobs "page" is the project's Jobs tab today: the link lands on
          the project where the tab lives, and ?tab=jobs is the deep link
          slice ③'s routed sidebar will consume (same entry-point pattern as
          ?state=). */}
      {pending > 0 && (
        <Alert
          type="info"
          showIcon
          message={t("files.notIndexedBar", { n: pending })}
          action={<Link to={`/projects/${projectId}?tab=jobs`}>{t("files.goToJobs")}</Link>}
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
      <Spin spinning={files.isFetching}>
        <FilesTable files={visible} canEdit={canEdit} onDelete={(name) => deleteFile.mutate(name)} />
      </Spin>
    </Space>
  );
}
