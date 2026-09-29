import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Modal, Select, Typography, message } from "antd";
import { sendOk } from "../../api/client";
import { projectFiles, projectTags } from "../../api/queries";
import type { TagEntry } from "../../api/types";

// What the modal edits: one row's tags (per-row edit, `current` is that
// row's tag list — Save sends the difference) or a selection (bulk, the
// chosen tags are added to or removed from every name).
export type TagTarget =
  | { kind: "row"; name: string; current: string[] }
  | { kind: "bulk"; names: string[] };

type TagOp = { name: string; method: "POST" | "DELETE"; tags: string[] };

// TagsIn bounds each tag at 50 characters (api/files_routes.py _TAG).
const MAX_TAG = 50;
// One request per file (the API tags one file at a time); a few in flight
// at once keeps a 500-row selection from opening 500 connections.
const CONCURRENCY = 4;

async function runOps(pid: string, ops: TagOp[]): Promise<string[]> {
  const failed = new Set<string>();
  for (let i = 0; i < ops.length; i += CONCURRENCY) {
    const chunk = ops.slice(i, i + CONCURRENCY);
    const settled = await Promise.allSettled(chunk.map((op) => sendOk(
      `/api/projects/${pid}/files/${encodeURIComponent(op.name)}/tags`, "files.tagFailed",
      { method: op.method, body: JSON.stringify({ tags: op.tags }) },
    )));
    settled.forEach((r, j) => { if (r.status === "rejected") failed.add(chunk[j].name); });
  }
  return [...failed];
}

// Tags are metadata, not input (spec 8): the backend tags while an index
// runs, so the modal is never frozen.
export default function TagsModal({ projectId, target, catalog, onClose }: {
  projectId: string;
  target: TagTarget | null;
  catalog: TagEntry[];
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  // The caller mounts the modal only while it is open, so the initial
  // value is always the current row's tags.
  const [value, setValue] = useState<string[]>(target?.kind === "row" ? target.current : []);

  const tags = [...new Set(value.map((v) => v.trim()).filter((v) => v !== ""))];
  const tooLong = tags.some((v) => v.length > MAX_TAG);
  const fileCount = target === null ? 0 : target.kind === "row" ? 1 : target.names.length;

  const apply = useMutation({
    mutationFn: (ops: TagOp[]) => runOps(projectId, ops),
    onSuccess: (failed) => {
      if (failed.length === 0) {
        message.success(target?.kind === "row"
          ? t("files.tagsUpdatedOne", { name: target.name })
          : t("files.tagsUpdated", { count: fileCount }));
      } else {
        message.warning(t("files.tagsPartial", { count: failed.length, names: failed.join(", ") }));
      }
      onClose();
    },
    // Some writes may have landed even when others failed: refresh both
    // the rows and the filter's catalog either way.
    onSettled: () => Promise.all([
      qc.invalidateQueries({ queryKey: projectFiles(projectId).queryKey }),
      qc.invalidateQueries({ queryKey: projectTags(projectId).queryKey }),
    ]),
  });

  const saveRow = () => {
    if (target?.kind !== "row") return;
    const add = tags.filter((v) => !target.current.includes(v));
    const remove = target.current.filter((v) => !tags.includes(v));
    const ops: TagOp[] = [
      ...(add.length > 0 ? [{ name: target.name, method: "POST" as const, tags: add }] : []),
      ...(remove.length > 0 ? [{ name: target.name, method: "DELETE" as const, tags: remove }] : []),
    ];
    if (ops.length === 0) onClose();
    else apply.mutate(ops);
  };
  const applyBulk = (method: "POST" | "DELETE") => {
    if (target?.kind !== "bulk") return;
    apply.mutate(target.names.map((name) => ({ name, method, tags })));
  };

  const footer = target?.kind === "bulk"
    ? [
        <Button key="cancel" onClick={onClose}>{t("common.cancel")}</Button>,
        <Button key="remove" disabled={tags.length === 0 || tooLong} loading={apply.isPending}
          onClick={() => applyBulk("DELETE")}>
          {t("files.tagsRemoveFrom", { count: fileCount })}
        </Button>,
        <Button key="add" type="primary" disabled={tags.length === 0 || tooLong} loading={apply.isPending}
          onClick={() => applyBulk("POST")}>
          {t("files.tagsAddTo", { count: fileCount })}
        </Button>,
      ]
    : [
        <Button key="cancel" onClick={onClose}>{t("common.cancel")}</Button>,
        <Button key="save" type="primary" disabled={tooLong} loading={apply.isPending} onClick={saveRow}>
          {t("common.save")}
        </Button>,
      ];

  return (
    <Modal
      open={target !== null}
      title={target?.kind === "row"
        ? t("files.editTagsTitle", { name: target.name })
        : t("files.tagSelectedTitle", { count: fileCount })}
      onCancel={onClose}
      footer={footer}
      destroyOnHidden
    >
      <Select
        mode="tags"
        autoFocus
        style={{ width: "100%" }}
        placeholder={t("files.tagsPlaceholder")}
        tokenSeparators={[","]}
        value={value}
        onChange={setValue}
        options={catalog.map((tag) => ({ label: tag.name, value: tag.name }))}
      />
      {tooLong && (
        <Typography.Text type="danger">{t("files.tagTooLong", { max: MAX_TAG })}</Typography.Text>
      )}
    </Modal>
  );
}
