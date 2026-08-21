import { useEffect, useRef, useState } from "react";
import { Drawer } from "antd";
import { useAuth } from "../stores/auth";

// Live job log viewer: native EventSource over the SSE route (Task 4).
// EventSource cannot send an Authorization header, so the access token is
// passed as a ?token= query param (backend accepts it on this route only).
// Reconnect after a drop is native: the browser replays Last-Event-ID.
export default function JobLogViewer({ jobId, open, onClose }: {
  jobId: string | null;
  open: boolean;
  onClose: () => void;
}) {
  const preRef = useRef<HTMLPreElement>(null);
  const [chunks, setChunks] = useState<string[]>([]);

  useEffect(() => {
    if (!open || !jobId) return;
    setChunks([]);
    // Read the token at stream-open time only: subscribing to the store would
    // re-create the EventSource on every 15-min rotation and replay the log.
    const token = useAuth.getState().accessToken;
    const url = `/api/jobs/${jobId}/logs${token ? `?token=${encodeURIComponent(token)}` : ""}`;
    const es = new EventSource(url);
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
    <Drawer title="任務日誌" open={open} onClose={onClose} size="large">
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
