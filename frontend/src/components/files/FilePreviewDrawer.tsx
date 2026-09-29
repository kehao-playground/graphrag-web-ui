import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Drawer, Spin, Typography } from "antd";
import { filePreview } from "../../api/queries";
import type { Locator } from "../../api/queries";
import { humanBytes } from "./indexState";

// Slice ① rows pass no locator — the drawer opens from a row and shows the
// head window. Slice ③ passes one picked by where the answer came from
// (see Locator in api/queries).
export type { Locator };

export default function FilePreviewDrawer({ projectId, name, locator, highlight, onClose }: {
  projectId: string;
  name: string | null;
  // The pin for the window: absent = head window (GET), otherwise one of
  // the two binding bodies (POST) — see Locator above.
  locator?: Locator;
  // The cited passage's text, marked and scrolled to when the window
  // matched it. The server found the same bytes, so an exact search of the
  // decoded window finds them too.
  highlight?: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const preview = useQuery({
    ...filePreview(projectId, name ?? "", locator ?? null),
    enabled: name !== null,
  });
  const data = preview.data;
  const at = data?.match && highlight ? data.text.indexOf(highlight) : -1;
  const markRef = useRef<HTMLElement>(null);
  useEffect(() => {
    markRef.current?.scrollIntoView?.({ block: "center" });
  }, [data, at]);

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
      {data && (
        <>
          {/* The head window (a row click) needs no header; a citation says
              whether the passage was found (R4-13). */}
          {locator && (
            <Typography.Paragraph type={data.match ? undefined : "warning"}>
              {t(data.match ? "files.previewPassage" : "files.previewPassageMissing", {
                size: humanBytes(data.total_size),
              })}
            </Typography.Paragraph>
          )}
          {/* errors="replace" on the backend keeps this printable; a binary
              file still renders as replacement characters rather than a
              broken layout. Long tokens wrap without splitting words (R4-14). */}
          <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", wordBreak: "normal", margin: 0 }}>
            {at < 0 ? data.text : (
              <>
                {data.text.slice(0, at)}
                <mark ref={markRef}>{data.text.slice(at, at + highlight!.length)}</mark>
                {data.text.slice(at + highlight!.length)}
              </>
            )}
          </pre>
        </>
      )}
    </Drawer>
  );
}
