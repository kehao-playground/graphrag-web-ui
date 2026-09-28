import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer } from "antd";
import { sseUrl } from "../api/client";

// Live job log viewer: native EventSource over the SSE route (Task 4);
// sseUrl() carries the auth rule. Reconnect after a drop is native: the
// browser replays Last-Event-ID.
export default function JobLogViewer({ jobId, open, title, onClose }: {
  jobId: string | null;
  open: boolean;
  // The caller's identity line for the job; the generic title otherwise.
  title?: string;
  onClose: () => void;
}) {
  const preRef = useRef<HTMLPreElement>(null);
  const { t } = useTranslation();
  const [chunks, setChunks] = useState<string[]>([]);

  useEffect(() => {
    if (!open || !jobId) return;
    setChunks([]);
    const es = new EventSource(sseUrl(`/api/jobs/${jobId}/logs`));
    // data is a JSON-encoded string chunk; json.dumps keeps it single-line.
    es.addEventListener("log", (e) => {
      setChunks((prev) => [...prev, JSON.parse((e as MessageEvent).data) as string]);
    });
    es.addEventListener("done", () => {
      // Terminal status: the server ends the response; stop listening locally.
      es.close();
    });
    return () => es.close();
  }, [open, jobId]);

  // Keep the newest line visible as the stream grows.
  useEffect(() => {
    const pre = preRef.current;
    if (pre) pre.scrollTop = pre.scrollHeight;
  }, [chunks]);

  return (
    <Drawer title={title ?? t("jobs.logsTitle")} open={open} onClose={onClose} size="large">
      <pre
        ref={preRef}
        style={{
          margin: 0, maxHeight: "70vh", overflow: "auto",
          fontSize: 12, lineHeight: 1.6, whiteSpace: "pre-wrap", wordBreak: "break-all",
        }}
      >
        {chunks.join("")}
      </pre>
    </Drawer>
  );
}
