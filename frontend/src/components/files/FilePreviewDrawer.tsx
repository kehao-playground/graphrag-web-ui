import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Descriptions, Drawer, Spin, Typography } from "antd";
import { filePreview } from "../../api/queries";
import type { Locator } from "../../api/queries";

// Slice ① rows pass no locator — the drawer opens from a row and shows the
// head window. Slice ③ passes one picked by where the answer came from
// (see Locator in api/queries).
export type { Locator };

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
    ...filePreview(projectId, name ?? "", locator ?? null),
    enabled: name !== null,
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
