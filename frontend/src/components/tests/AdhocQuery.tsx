import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Select, Space, message } from "antd";
import type { Citation, QueryMethod, QueryTimings, QuestionSet } from "../../api/types";
import { api, detailOf, messageOfBody } from "../../api/client";
import { useAuth } from "../../stores/auth";
import AnswerView from "./AnswerView";
import { methodOptions } from "./methods";

// Query answers are multi-paragraph prose; the backend default response_type.
const RESPONSE_TYPE = "multiple paragraphs";

// The workbench's ad-hoc mode (spec §9.2): the interactive SSE path, kept
// exactly as QueryPanel had it, with its rendering lifted into AnswerView
// and a one-action save button into a question set.
// zh-TW: the save button's literal label is 存成題目.
export default function AdhocQuery({ projectId, canUse }: { projectId: string; canUse: boolean }) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [method, setMethod] = useState<QueryMethod>("local");
  const [query, setQuery] = useState("");
  const [chunks, setChunks] = useState<string[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [timings, setTimings] = useState<QueryTimings | null>(null);
  const [streaming, setStreaming] = useState(false);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveSet, setSaveSet] = useState<string>();
  const esRef = useRef<EventSource | null>(null);

  // Close on unmount (mode switch): unlike job logs there is no resume, and
  // auto-reconnect would replay the query and double-charge the rate limit.
  useEffect(() => () => esRef.current?.close(), []);

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

  // Save-target sets: fetched only while the save dialog is open, so a
  // query session that never saves pays no extra request. Quiet on failure
  // (a missing catalog costs the save target list, not the query).
  const sets = useQuery({
    queryKey: ["projects", projectId, "question-sets"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/question-sets`);
      if (!r.ok) throw new Error(await detailOf(r, "workbench.loadSetsFailed"));
      return (await r.json()) as { sets: QuestionSet[] };
    },
    enabled: saveOpen,
    retry: false,
  });

  // One action saves the QUESTION (not the answer) into the chosen set;
  // answers are produced by runs, and this save button is how a good ad-hoc
  // question joins the set the next batch will ask.
  // zh-TW: the button's literal label is 存成題目.
  const saveQuestion = useMutation({
    mutationFn: async (setId: string) => {
      const r = await api(`/api/projects/${projectId}/question-sets/${setId}/questions`, {
        method: "POST",
        body: JSON.stringify({ text: query.trim() }),
      });
      if (!r.ok) throw new Error(await detailOf(r, "workbench.saveFailed"));
    },
    onSuccess: () => {
      message.success(t("workbench.saved"));
      setSaveOpen(false);
      qc.invalidateQueries({ queryKey: ["projects", projectId, "question-sets"] });
    },
    onError: (e) => message.error(e.message),
  });

  const busy = streaming;
  const canSave = !busy && query.trim().length > 0;
  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Space direction="vertical" size="small" style={{ width: "100%" }}>
        <Select
          value={method}
          onChange={setMethod}
          options={methodOptions(t)}
          style={{ width: 160 }}
          disabled={busy}
        />
        <Input.TextArea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) run();
          }}
          placeholder={t("workbench.adhocPlaceholder")}
          rows={3}
          disabled={busy}
        />
        <Button type="primary" onClick={run} disabled={!canUse || busy || !query.trim()}>
          {t("workbench.adhocRun")}
        </Button>
        {canSave && (
          <Button
            onClick={() => {
              setSaveSet(undefined);
              setSaveOpen(true);
            }}
          >
            {t("workbench.saveAsQuestion")}
          </Button>
        )}
      </Space>

      <AnswerView answer={chunks.join("")} citations={citations} timings={timings} streaming={busy} />

      <Modal
        open={saveOpen}
        title={t("workbench.saveTitle")}
        okText={t("workbench.saveOk")}
        cancelText={t("common.cancel")}
        okButtonProps={{ disabled: !saveSet }}
        confirmLoading={saveQuestion.isPending}
        onOk={() => saveSet && saveQuestion.mutate(saveSet)}
        onCancel={() => setSaveOpen(false)}
      >
        <Select
          style={{ width: "100%" }}
          placeholder={t("workbench.setPlaceholder")}
          value={saveSet}
          onChange={setSaveSet}
          loading={sets.isPending}
          options={(sets.data?.sets ?? []).map((s) => ({ label: s.name, value: s.id }))}
        />
      </Modal>
    </Space>
  );
}
