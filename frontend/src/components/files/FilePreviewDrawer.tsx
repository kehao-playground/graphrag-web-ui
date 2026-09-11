import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Descriptions, Drawer, Spin, Typography } from "antd";
import { api, detailOf } from "../../api/client";

// A locator pins WHERE in the document the window should center on. Slice ①
// rows pass none — the drawer opens from a row and shows the head window.
// Slice ③ passes {resultId, entryId} for a stored run or {passage} for an
// ad-hoc query, picking the variant by where the answer came from (spec
// §7.4); entryId is a number to match Citation.ids.
export type Locator = { resultId: string; entryId: number } | { passage: string };

type PreviewOut = {
  text: string;
  offset: number;
  total_size: number;
  match: boolean;
};

export default function FilePreviewDrawer({ projectId, name, locator, onClose }: {
  projectId: string;
  name: string | null;
  // The pin for the window: absent = head window (GET), otherwise one of
  // the two binding bodies (POST) — see Locator above.
  locator?: Locator;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const preview = useQuery({
    // locator participates in the key so a slice-3 caller re-fetches when
    // the pin changes even if the document stays the same.
    queryKey: ["projects", projectId, "files", name, "preview", locator ?? null],
    queryFn: async (): Promise<PreviewOut> => {
      const base = `/api/projects/${projectId}/files/${encodeURIComponent(name!)}`;
      // GET keeps slice ①'s head window untouched; a locator always POSTs
      // its binding body (spec §7.4) — {result_id, entry_id} makes the
      // server re-read the stored passage, {passage} searches the document
      // for text the ad-hoc answer already cited.
      const r = !locator
        ? await api(base + "/preview")
        : await api(base + "/preview", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify("passage" in locator
              ? { passage: locator.passage }
              : { result_id: locator.resultId, entry_id: locator.entryId }),
          });
      if (!r.ok) throw new Error(await detailOf(r, "files.previewLoadFailed"));
      return (await r.json()) as PreviewOut;
    },
    enabled: name !== null,
    retry: false,
  });

  return (
    <Drawer
      open={name !== null}
      onClose={onClose}
      title={name ?? ""}
      width={640}
      destroyOnClose
    >
      {preview.isFetching && <Spin />}
      {preview.error && <Typography.Text type="danger">{preview.error.message}</Typography.Text>}
      {preview.data && (
        <>
          <Descriptions size="small" column={3} style={{ marginBottom: 12 }}>
            <Descriptions.Item label={t("files.previewMatch")}>
              {preview.data.match ? t("files.previewMatchHit") : t("files.previewMiss")}
            </Descriptions.Item>
            <Descriptions.Item label={t("files.previewOffset")}>
              {preview.data.offset}
            </Descriptions.Item>
            <Descriptions.Item label={t("files.previewTotal")}>
              {preview.data.total_size}
            </Descriptions.Item>
          </Descriptions>
          {/* errors="replace" on the backend keeps this printable; a binary
              file still renders as replacement characters rather than a
              broken layout. */}
          <Typography.Paragraph>
            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", margin: 0 }}>
              {preview.data.text}
            </pre>
          </Typography.Paragraph>
        </>
      )}
    </Drawer>
  );
}
