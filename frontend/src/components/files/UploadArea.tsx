import { useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Upload, message } from "antd";
import type { UploadProps } from "antd";
import { apiJson } from "../../api/client";
import { invalidateProjectFiles } from "../../api/invalidate";
import type { UploadedFile } from "../../api/types";
import { humanBytes } from "./indexState";

// One keyed toast per upload drop: progress while it runs, then the summary.
const UPLOAD_KEY = "files-upload";

// The documents drop zone: the input type's extensions, the per-file cap
// checked up front, and one toast and one listing refresh per drop.
export default function UploadArea({ projectId, accept, maxFileBytes, frozen }: {
  projectId: string;
  accept: string;
  // The server's per-file cap, once the listing has answered.
  maxFileBytes: number | undefined;
  frozen: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  // Files, tags and the health badges all move with an upload.
  const invalidateFiles = () => invalidateProjectFiles(qc, projectId);

  // One drop = one batch (R4-07): antd calls customRequest once per file,
  // all in the same tick, so an in-flight counter marks the batch's end.
  // The drop gets one keyed toast — progress, then a summary — and one
  // listing refresh, instead of a toast and a refetch per file.
  const batch = useRef({ pending: 0, total: 0, ok: [] as string[], failed: [] as string[], solo: "" });

  // `reason` goes into the batch summary after the file's name; `solo` is
  // the whole sentence shown when the drop was that one file.
  const settleUpload = (name: string, error: { reason: string; solo: string } | null) => {
    const b = batch.current;
    if (error === null) b.ok.push(name);
    else {
      b.failed.push(t("files.uploadFailedItem", { name, reason: error.reason }));
      b.solo = error.solo;
    }
    b.pending -= 1;
    if (b.pending > 0) {
      message.open({
        key: UPLOAD_KEY, type: "loading", duration: 0,
        content: t("files.uploadProgress", { done: b.ok.length + b.failed.length, total: b.total }),
      });
      return;
    }
    const { ok, failed, total, solo } = b;
    batch.current = { pending: 0, total: 0, ok: [], failed: [], solo: "" };
    if (failed.length === 0) {
      message.open({
        key: UPLOAD_KEY, type: "success",
        content: total === 1 ? t("files.uploaded", { name: ok[0] }) : t("files.uploadedMany", { count: total }),
      });
    } else if (total === 1) {
      message.open({ key: UPLOAD_KEY, type: "error", content: solo });
    } else {
      message.open({
        key: UPLOAD_KEY, type: ok.length === 0 ? "error" : "warning", duration: 8,
        content: t("files.uploadPartial", { ok: ok.length, count: total, names: failed.join("; ") }),
      });
    }
    if (ok.length > 0) void invalidateFiles();
  };

  // customRequest keeps the multipart POST inside api() so the auth header
  // and 401-retry apply; the browser sets the multipart boundary itself.
  const customRequest: UploadProps["customRequest"] = async ({ file, onSuccess, onError }) => {
    const f = file as File;
    const b = batch.current;
    b.pending += 1;
    b.total += 1;
    message.open({
      key: UPLOAD_KEY, type: "loading", duration: 0,
      content: t("files.uploadProgress", { done: b.ok.length + b.failed.length, total: b.total }),
    });
    // The server is the authority on the cap; checking here only spares
    // the user a doomed transfer of a file it will refuse (R4-08).
    if (maxFileBytes !== undefined && f.size > maxFileBytes) {
      const max = humanBytes(maxFileBytes);
      const solo = t("files.tooLarge", { name: f.name, max });
      onError?.(new Error(solo));
      settleUpload(f.name, { reason: t("files.tooLargeReason", { max }), solo });
      return;
    }
    const fd = new FormData();
    fd.append("file", f);
    try {
      const out = await apiJson<UploadedFile>(
        `/api/projects/${projectId}/files`, "files.uploadFailed", { method: "POST", body: fd },
      );
      onSuccess?.(out, file);
      settleUpload(f.name, null);
    } catch (e) {
      // HTTP errors arrive localized; api() rethrows network-level
      // failures, which surface the same way
      const error = e instanceof Error ? e : new Error(t("files.uploadNetworkFailed"));
      onError?.(error);
      settleUpload(f.name, { reason: error.message, solo: error.message });
    }
  };

  return (
    <Upload.Dragger
      accept={accept}
      customRequest={customRequest}
      showUploadList={false}
      multiple
      disabled={frozen}
    >
      <p className="ant-upload-text">{t("files.uploadHint")}</p>
      <p className="ant-upload-hint">
        {t("files.acceptHint", { accept })}
        {maxFileBytes !== undefined && ` · ${t("files.limitHint", { max: humanBytes(maxFileBytes) })}`}
      </p>
    </Upload.Dragger>
  );
}
