import { useEffect, useRef, useState } from "react";
import { Button, Collapse, Input, Select, Skeleton, Space, Typography, message } from "antd";
import type { Citation, QueryMethod, QueryTimings } from "../api/types";
import { useAuth } from "../stores/auth";

const METHOD_LABEL: Record<QueryMethod, string> = {
  local: "區域", global: "全域", drift: "DRIFT", basic: "基本",
};
const METHOD_OPTIONS = (["local", "global", "drift", "basic"] as const).map((v) => ({
  label: METHOD_LABEL[v], value: v,
}));

// Query answers are multi-paragraph prose; the backend default response_type.
const RESPONSE_TYPE = "multiple paragraphs";

export default function QueryPanel({ projectId, canUse }: { projectId: string; canUse: boolean }) {
  const [method, setMethod] = useState<QueryMethod>("local");
  const [query, setQuery] = useState("");
  const [chunks, setChunks] = useState<string[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [timings, setTimings] = useState<QueryTimings | null>(null);
  const [streaming, setStreaming] = useState(false);
  const answerRef = useRef<HTMLDivElement>(null);
  const esRef = useRef<EventSource | null>(null);

  // Close on unmount (tab switch): unlike job logs there is no resume, and
  // auto-reconnect would replay the query and double-charge the rate limit.
  useEffect(() => () => esRef.current?.close(), []);

  // Keep the newest line visible as the answer grows.
  useEffect(() => {
    const el = answerRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [chunks]);

  const run = () => {
    const q = query.trim();
    if (!canUse || streaming || !q) return;
    setChunks([]);
    setCitations([]);
    setTimings(null);
    setStreaming(true);
    // Read the token at stream-open time only (same rationale as JobLogViewer:
    // subscribing to the store would replay the query on token rotation).
    const token = useAuth.getState().accessToken;
    const url =
      `/api/projects/${projectId}/query/stream` +
      `?method=${method}` +
      `&query=${encodeURIComponent(q)}` +
      `&response_type=${encodeURIComponent(RESPONSE_TYPE)}` +
      `&token=${encodeURIComponent(token ?? "")}`;
    const es = new EventSource(url);
    esRef.current = es;

    // data is a JSON-encoded string fragment; json.dumps keeps it single-line.
    es.addEventListener("chunk", (e) => {
      setChunks((prev) => [...prev, JSON.parse((e as MessageEvent).data) as string]);
    });
    es.addEventListener("citations", (e) => {
      setCitations(JSON.parse((e as MessageEvent).data) as Citation[]);
    });
    es.addEventListener("done", (e) => {
      setTimings(JSON.parse((e as MessageEvent).data) as QueryTimings);
      setStreaming(false);
      es.close();
    });
    // One listener covers both failure shapes: an SSE `event: error` frame
    // carries data {"detail"}, while a transport failure (network drop or a
    // pre-stream 4xx JSON response — EventSource never exposes that body)
    // fires an error with no data. Both close: no auto-reconnect for query.
    es.addEventListener("error", (e) => {
      const raw = (e as MessageEvent).data;
      let detail: string | null = null;
      if (typeof raw === "string") {
        try {
          detail = (JSON.parse(raw) as { detail?: string }).detail ?? null;
        } catch {
          // Malformed payload → generic message below.
        }
      }
      message.error(detail ?? "查詢失敗,請稍後再試");
      setStreaming(false);
      es.close();
    });
  };

  const busy = streaming;
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Select
          value={method}
          onChange={setMethod}
          options={METHOD_OPTIONS}
          style={{ width: 160 }}
          disabled={busy}
        />
        <Input.TextArea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) run();
          }}
          placeholder="輸入查詢問題"
          rows={3}
          disabled={busy}
        />
        <Button type="primary" onClick={run} disabled={!canUse || busy || !query.trim()}>
          執行查詢
        </Button>
      </Space>

      {(busy || chunks.length > 0) && (
        <div ref={answerRef} style={{ maxHeight: "40vh", overflow: "auto" }}>
          <Typography.Paragraph style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}>
            {chunks.join("")}
          </Typography.Paragraph>
        </div>
      )}

      {busy && citations.length === 0 ? (
        <Skeleton active paragraph={{ rows: 2 }} title={false} />
      ) : citations.length > 0 ? (
        <Collapse
          items={[{
            key: "citations",
            label: `引用 (${citations.length})`,
            children: citations.map((c, i) => (
              <div key={`${c.label}-${i}`} style={{ marginBottom: 8 }}>
                <Typography.Text strong>
                  {c.label} #{c.ids.join(", ")}
                </Typography.Text>
                {c.entries.map((en) => (
                  <Typography.Paragraph
                    key={en.id}
                    type="secondary"
                    style={{ whiteSpace: "pre-wrap", marginBottom: 0 }}
                  >
                    {en.text ?? "—"}
                  </Typography.Paragraph>
                ))}
              </div>
            )),
          }]}
        />
      ) : null}

      {timings && (
        <Typography.Text type="secondary">
          {`frames ${Math.round(timings.frames_ms)}ms · 搜尋 ${Math.round(timings.search_ms)}ms · 引用 ${Math.round(timings.citations_ms)}ms · 總計 ${Math.round(timings.total_ms)}ms`}
        </Typography.Text>
      )}
    </Space>
  );
}
