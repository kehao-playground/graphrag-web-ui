import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Input, Modal, Select, Space, Typography, message } from "antd";
import type { Citation, QueryMethod, QuestionSet, QueryTimings } from "../../api/types";
import { apiJson, messageOfBody, sendOk, sseUrl } from "../../api/client";
import { questionSets } from "../../api/queries";
import AnswerView from "./AnswerView";
import { methodOptions } from "./methods";

// Query answers are multi-paragraph prose; the backend default response_type.
const RESPONSE_TYPE = "multiple paragraphs";
// The save dialog's pseudo-option: create a set and save into it.
const NEW_SET = "__new__";

// The workbench's ad-hoc mode (spec §9.2): the interactive SSE path, kept
// exactly as QueryPanel had it, with its rendering lifted into AnswerView
// and a one-action save button into a question set (project:edit_content).
// zh-TW: the save button's literal label is 存成題目.
export default function AdhocQuery({ projectId, canEdit }: {
  projectId: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [method, setMethod] = useState<QueryMethod>("local");
  const [query, setQuery] = useState("");
  const [chunks, setChunks] = useState<string[]>([]);
  const [citations, setCitations] = useState<Citation[]>([]);
  const [timings, setTimings] = useState<QueryTimings | null>(null);
  const [streaming, setStreaming] = useState(false);
  // Wall-clock start of the running query, for the elapsed counter (R4-11).
  const [startedAt, setStartedAt] = useState(0);
  const [now, setNow] = useState(0);
  const [saveOpen, setSaveOpen] = useState(false);
  const [saveSet, setSaveSet] = useState<string>();
  const [newSetName, setNewSetName] = useState("");
  const esRef = useRef<EventSource | null>(null);
  const cancelledRef = useRef(false);

  // Close on unmount (mode switch): unlike job logs there is no resume, and
  // auto-reconnect would replay the query and double-charge the rate limit.
  useEffect(() => () => {
    cancelledRef.current = true;
    esRef.current?.close();
  }, []);

  useEffect(() => {
    if (!streaming) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [streaming]);

  // Closing the EventSource ends the request; the server stops the search
  // when the client goes away. The partial answer stays on screen.
  const cancel = () => {
    cancelledRef.current = true;
    esRef.current?.close();
    setStreaming(false);
    message.info(t("query.cancelled"));
  };

  const run = async () => {
    const q = query.trim();
    if (streaming || !q) return;
    setChunks([]);
    setCitations([]);
    setTimings(null);
    setStreaming(true);
    const t0 = Date.now();
    setStartedAt(t0);
    setNow(t0);
    // The ticket is minted per query (F24-01); Cancel or unmount while it
    // is in flight leaves cancelledRef set, and no stream is opened.
    cancelledRef.current = false;
    let url: string;
    try {
      url = await sseUrl(`/api/projects/${projectId}/query/stream`, {
        method, query: q, response_type: RESPONSE_TYPE,
      }, "query.failedRetry");
    } catch (err) {
      if (!cancelledRef.current) {
        message.error(err instanceof Error ? err.message : t("query.failedRetry"));
        setStreaming(false);
      }
      return;
    }
    if (cancelledRef.current) return;
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
    // carries data {"detail", "code"?} — a mid-stream interruption, or a
    // refusal (rate limit, not indexed) sent as the stream's only frame —
    // while a transport failure (network drop, or an auth/validation 4xx
    // whose body EventSource never exposes) fires an error with no data.
    // Both close: no auto-reconnect for query.
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
  const sets = useQuery({ ...questionSets(projectId), enabled: saveOpen, meta: { silent: true } });

  // One action saves the QUESTION (not the answer) into the chosen set;
  // answers are produced by runs, and this save button is how a good ad-hoc
  // question joins the set the next batch will ask.
  // zh-TW: the button's literal label is 存成題目.
  // With no set yet the target defaults to a new one (R4-03): the dialog
  // asks for its name instead of showing an empty dropdown.
  const target = saveSet ?? (sets.data?.sets.length === 0 ? NEW_SET : undefined);
  const saveQuestion = useMutation({
    mutationFn: async (setId: string) => {
      const sid = setId !== NEW_SET ? setId : (await apiJson<QuestionSet>(
        `/api/projects/${projectId}/question-sets`, "workbench.createSetFailed",
        { method: "POST", body: JSON.stringify({ name: newSetName.trim() }) },
      )).id;
      await sendOk(
        `/api/projects/${projectId}/question-sets/${sid}/questions`, "workbench.saveFailed",
        { method: "POST", body: JSON.stringify({ text: query.trim() }) },
      );
    },
    onSuccess: () => {
      message.success(t("workbench.saved"));
      setSaveOpen(false);
      void qc.invalidateQueries({ queryKey: questionSets(projectId).queryKey });
    },
  });

  const busy = streaming;
  const methodHint: Record<QueryMethod, string> = {
    local: t("query.hintLocal"),
    global: t("query.hintGlobal"),
    drift: t("query.hintDrift"),
    basic: t("query.hintBasic"),
  };
  const canSave = canEdit && !busy && query.trim().length > 0;
  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      <Space orientation="vertical" size="small" style={{ width: "100%" }}>
        <Select
          value={method}
          onChange={setMethod}
          options={methodOptions(t)}
          style={{ width: 160 }}
          disabled={busy}
        />
        <Typography.Text type="secondary">{methodHint[method]}</Typography.Text>
        <Input.TextArea
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onPressEnter={(e) => {
            if (!e.shiftKey) void run();
          }}
          placeholder={t("workbench.adhocPlaceholder")}
          rows={3}
          disabled={busy}
        />
        <Space>
          <Button type="primary" onClick={() => void run()} disabled={busy || !query.trim()}>
            {t("workbench.adhocRun")}
          </Button>
          {busy && (
            <>
              <Button onClick={cancel}>{t("common.cancel")}</Button>
              <Typography.Text type="secondary">
                {t("query.elapsed", { seconds: Math.max(0, Math.floor((now - startedAt) / 1000)) })}
              </Typography.Text>
            </>
          )}
        </Space>
        {canSave && (
          <Button
            onClick={() => {
              setSaveSet(undefined);
              setNewSetName("");
              setSaveOpen(true);
            }}
          >
            {t("workbench.saveAsQuestion")}
          </Button>
        )}
      </Space>

      <AnswerView
        projectId={projectId}
        answer={chunks.join("")}
        citations={citations}
        timings={timings}
        streaming={busy}
        // An ad-hoc answer has no stored artifacts to bind a citation to,
        // so its links pin on the passage the citations payload carries.
        origin={null}
      />

      <Modal
        open={saveOpen}
        title={t("workbench.saveTitle")}
        okText={t("workbench.saveOk")}
        cancelText={t("common.cancel")}
        okButtonProps={{
          disabled: !target || (target === NEW_SET && !newSetName.trim()),
        }}
        confirmLoading={saveQuestion.isPending}
        onOk={() => target && saveQuestion.mutate(target)}
        onCancel={() => setSaveOpen(false)}
      >
        <Space orientation="vertical" size="small" style={{ width: "100%" }}>
          <Select
            style={{ width: "100%" }}
            placeholder={t("workbench.setPlaceholder")}
            value={target}
            onChange={setSaveSet}
            loading={sets.isPending}
            options={[
              ...(sets.data?.sets ?? []).map((s) => ({ label: s.name, value: s.id })),
              { label: t("workbench.newSetOption"), value: NEW_SET },
            ]}
          />
          {target === NEW_SET && (
            <Input
              aria-label={t("workbench.newSetName")}
              placeholder={t("workbench.newSetName")}
              maxLength={200}
              value={newSetName}
              onChange={(e) => setNewSetName(e.target.value)}
            />
          )}
        </Space>
      </Modal>
    </Space>
  );
}
