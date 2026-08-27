import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Collapse, Input, Select, Skeleton, Space, Typography, message } from "antd";
import type { Citation, QueryMethod, QueryTimings } from "../api/types";
import { messageOfBody } from "../api/client";
import { useAuth } from "../stores/auth";

// Query answers are multi-paragraph prose; the backend default response_type.
const RESPONSE_TYPE = "multiple paragraphs";

export default function QueryPanel({ projectId, canUse }: { projectId: string; canUse: boolean }) {
  const { t } = useTranslation();
  // drift keeps the endonym "DRIFT" in every locale (identifier, not copy).
  const METHOD_OPTIONS = (["local", "global", "drift", "basic"] as const).map((v) => ({
    label:
      v === "local" ? t("query.methodLocal")
      : v === "global" ? t("query.methodGlobal")
      : v === "drift" ? t("query.methodDrift")
      : t("query.methodBasic"),
    value: v,
  }));
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
      // No token in proxy mode (cookie auth); an empty token= param would
      // just read as an invalid bearer upstream (spec §6.4).
      (token ? `&token=${encodeURIComponent(token)}` : "");
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
    // carries data {"detail", "code"?}, while a transport failure (network drop or a
    // pre-stream 4xx JSON response — EventSource never exposes that body)
    // fires an error with no data. Both close: no auto-reconnect for query.
    es.addEventListener("error", (e) => {
      const raw = (e as MessageEvent).data;
      let body: Record<string, unknown> = {};
      if (typeof raw === "string") {
        try {
          body = JSON.parse(raw) as Record<string, unknown>;
        } catch {
          // Malformed payload → generic message below.
        }
      }
      // Error frames share the HTTP envelope: a known code localizes
      // (e.g. query_interrupted), else the detail verbatim, else the fallback.
      message.error(messageOfBody(body, "query.failedRetry"));
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
          placeholder={t("query.placeholder")}
          rows={3}
          disabled={busy}
        />
        <Button type="primary" onClick={run} disabled={!canUse || busy || !query.trim()}>
          {t("query.run")}
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
            label: t("query.citations", { count: citations.length }),
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
          {t("query.timings", {
            frames: Math.round(timings.frames_ms),
            search: Math.round(timings.search_ms),
            citations: Math.round(timings.citations_ms),
            total: Math.round(timings.total_ms),
          })}
        </Typography.Text>
      )}
    </Space>
  );
}
