import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useBlocker } from "react-router-dom";
import { dump as yamlDump, load as yamlLoad } from "js-yaml";
import {
  Alert, Button, Collapse, Descriptions, Input, InputNumber, Modal, Pagination, Popconfirm, Radio,
  Space, Table, Tag, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { ApiRequestError, apiJson, sendOk } from "../api/client";
import {
  jobsPreflight, projectEnv, projectSettings, settingsVersions, settingsVersionsKey,
} from "../api/queries";
import type { DryRunOut, EnvKeyOut, SettingsConflict, SettingsVersionDetail } from "../api/types";
import { isFrozen } from "./project/frozen";
import { formatDateTime } from "../i18n/format";

const { TextArea } = Input;
const { Text } = Typography;

// Versions per page of the history; the server pages it (R3-10).
const VERSIONS_PAGE_SIZE = 20;

interface Conflict {
  currentContent: string;
  currentHash: string;
  myContent: string;
}

// A 400 from a save, kept under the editor until the next save (R4-35).
interface SaveError {
  text: string;
  line?: number;
  column?: number;
}

// Form mode edits these paths in the parsed document (spec §6.5: input.* is
// locked at creation and shown read-only).
const COMPLETION_PATHS = ["model", "model_provider", "auth_method"] as const;
const MODEL_SECTIONS = [
  "completion_models.default_completion_model",
  "embedding_models.default_embedding_model",
] as const;
const FORM_FIELDS: Array<[string, readonly string[]]> = [
  ...MODEL_SECTIONS.map((s): [string, readonly string[]] => [s, COMPLETION_PATHS]),
  ["chunking", ["size", "overlap"]],
];

type FormValue = string | number;

// Form mode parses the current YAML. Empty content parses to undefined and
// "~" to null (no throw), and scalars/arrays parse fine but are not maps —
// treat every non-plain-object base as broken: degrade the display instead
// of crashing the render.
function plainObject(text: string): Record<string, unknown> | null {
  try {
    const v = yamlLoad(text);
    return v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : null;
  } catch {
    return null;
  }
}

// Walks a dotted path ("completion_models.default_completion_model.model")
// through nested maps; anything missing or not a map on the way is undefined.
function atPath(doc: Record<string, unknown>, path: string): unknown {
  let node: unknown = doc;
  for (const p of path.split(".")) {
    if (node === null || typeof node !== "object" || Array.isArray(node)) return undefined;
    node = (node as Record<string, unknown>)[p];
  }
  return node;
}

// PyYAML's message is context/problem lines interleaved with
// `in "<unicode string>", line N, column M` marks: keep the prose, report
// the last mark (the problem, not the context) as the location.
function yamlProblem(reason: string): Omit<SaveError, "text"> & { prose: string } {
  const marks = [...reason.matchAll(/line (\d+), column (\d+)/g)];
  const last = marks.at(-1);
  const prose = reason.split("\n").map((l) => l.trim())
    .filter((l) => l && !l.startsWith("in \"")).join("; ");
  return { prose, line: last ? Number(last[1]) : undefined, column: last ? Number(last[2]) : undefined };
}

export default function SettingsPanel({ projectId, canEdit }: {
  projectId: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t, i18n } = useTranslation();
  const [mode, setMode] = useState<"yaml" | "form">("yaml");
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [saveError, setSaveError] = useState<SaveError | null>(null);
  const [viewVersion, setViewVersion] = useState<SettingsVersionDetail | null>(null);
  const [dryRun, setDryRun] = useState<{ ok: boolean; output: string } | null>(null);
  const [envKey, setEnvKey] = useState("");
  const [envValue, setEnvValue] = useState("");
  const editor = useRef<TextAreaRef>(null);

  const settings = useQuery(projectSettings(projectId));
  // An index/update freezes settings and keys (backend 409 project_indexing):
  // say so up front and disable the writes, as the Documents pane does.
  const preflight = useQuery(jobsPreflight(projectId));
  const frozen = isFrozen(preflight.data);
  const writable = canEdit && !frozen;

  // Local edit keyed by the server hash it was based on: while the hash
  // matches, the local text wins; a new server hash (successful save,
  // reload, restore) resyncs automatically — derived during render.
  const [edit, setEdit] = useState<{ hash: string; content: string } | null>(null);
  const serverContent = settings.data?.content ?? "";
  const diskHash = settings.data?.content_hash ?? "";
  const content = edit && edit.hash === diskHash ? edit.content : serverContent;
  const setContent = (c: string) => setEdit({ hash: diskHash, content: c });
  const [versionsPage, setVersionsPage] = useState(1);
  const versions = useQuery(settingsVersions(projectId, {
    limit: VERSIONS_PAGE_SIZE, offset: (versionsPage - 1) * VERSIONS_PAGE_SIZE,
  }));
  const env = useQuery(projectEnv(projectId));
  const invalidateEnv = () => qc.invalidateQueries({ queryKey: projectEnv(projectId).queryKey });

  // Draft keyed by the server hash it was typed against (same pattern as the
  // YAML editor): a new server hash (save, reload, restore, concurrent edit)
  // discards the draft so stale values cannot be written back.
  const [draft, setDraft] = useState<{ hash: string; values: Record<string, FormValue> }>({ hash: "", values: {} });
  const formDraft = draft.hash === diskHash ? draft.values : {};
  // Parsed only when something reads it: the form view, or a form draft
  // that the dirty check compares against.
  const base = mode === "form" || Object.keys(formDraft).length > 0 ? plainObject(content) : null;
  const parsed = base ?? {};
  const formValue = (path: string): FormValue => {
    const v = atPath(parsed, path);
    return typeof v === "number" ? v : String(v ?? "");
  };
  const formDirty = Object.entries(formDraft).some(([path, v]) => v !== formValue(path));
  const dirty = content !== serverContent || formDirty;

  // Unsaved edits survive neither a route change nor a reload silently
  // (R4-22): the router blocks in-app navigation, beforeunload the rest.
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  async function fetchVersion(id: number): Promise<SettingsVersionDetail | null> {
    try {
      return await apiJson<SettingsVersionDetail>(
        `/api/projects/${projectId}/settings/versions/${id}`, "settings.loadVersionFailed",
      );
    } catch (e) {
      message.error((e as Error).message);
      return null;
    }
  }

  // Shared PUT (direct saves and restores). Its onError owns the reporting,
  // so the shared toast stays out: only settings_conflict is an edit
  // conflict (R4-01 — project_indexing is a 409 too); a 400 is a validation
  // result shown inline under the editor; anything else is a toast. The
  // draft is kept in every failure case.
  const save = useMutation({
    meta: { silent: true },
    mutationFn: ({ content: c, expectedHash }: { content: string; expectedHash: string }) =>
      sendOk(`/api/projects/${projectId}/settings`, "settings.saveFailed", {
        method: "PUT",
        body: JSON.stringify({ content: c, expected_hash: expectedHash }),
      }),
    onMutate: () => setSaveError(null),
    onError: (e, { content: c }) => {
      if (e instanceof ApiRequestError && e.status === 409 && e.body.code === "settings_conflict") {
        const body = e.body as SettingsConflict;
        setConflict({
          currentContent: body.current_content,
          currentHash: body.current_hash,
          myContent: c,
        });
        return;
      }
      setConflict(null);
      if (e instanceof ApiRequestError && e.status === 400) {
        const params = e.body.params as Record<string, unknown> | undefined;
        if (e.body.code === "settings_invalid_yaml" && typeof params?.reason === "string") {
          const { prose, line, column } = yamlProblem(params.reason);
          setSaveError({ text: t("settings.invalidYaml", { reason: prose }), line, column });
        } else {
          setSaveError({ text: e.message });
        }
        return;
      }
      message.error(e.message);
    },
    onSuccess: async () => {
      setConflict(null);
      message.success(t("settings.saved"));
      await qc.invalidateQueries({ queryKey: projectSettings(projectId).queryKey });
      await qc.invalidateQueries({ queryKey: settingsVersionsKey(projectId) });
    },
  });

  // A failed dry run is a result, shown inline — not a toast.
  const dryRunMutation = useMutation({
    meta: { silent: true },
    mutationFn: () => apiJson<DryRunOut>(
      `/api/projects/${projectId}/dry-run`, "settings.dryRunFailed", { method: "POST" },
    ),
    onSuccess: (out) => setDryRun({ ok: out.ok, output: out.output }),
    onError: (e) => setDryRun({ ok: false, output: e.message }),
  });

  const patchEnv = useMutation({
    mutationFn: () => sendOk(`/api/projects/${projectId}/env`, "settings.envSetFailed", {
      method: "PATCH",
      body: JSON.stringify({ key: envKey, value: envValue }),
    }),
    onSuccess: async () => {
      setEnvKey("");
      setEnvValue("");
      message.success(t("settings.envSet"));
      await invalidateEnv();
    },
  });

  const deleteEnv = useMutation({
    mutationFn: (key: string) => sendOk(
      `/api/projects/${projectId}/env/${encodeURIComponent(key)}`, "settings.envDeleteFailed",
      { method: "DELETE" },
    ),
    onSuccess: async () => {
      message.success(t("settings.envDeleted"));
      await invalidateEnv();
    },
  });

  const setFormDraft = (path: string, v: FormValue | null) => {
    const values = { ...formDraft };
    if (v === null) delete values[path];
    else values[path] = v;
    setDraft({ hash: diskHash, values });
  };
  const textField = (path: string) => ({
    value: String(formDraft[path] ?? formValue(path)),
    onChange: (e: { target: { value: string } }) => setFormDraft(path, e.target.value),
    disabled: !writable,
  });
  // Chunk sizes are integers in graphrag's schema: an InputNumber keeps
  // them numbers, so the saved YAML says `size: 900`, not `size: '900'`.
  const numberField = (path: string) => {
    const shown = formDraft[path] ?? formValue(path);
    return {
      value: shown === "" ? null : Number(shown),
      onChange: (v: number | null) => setFormDraft(path, v),
      precision: 0,
      min: 0,
      disabled: !writable,
      style: { width: "100%" },
    };
  };

  function applyFormEdits(): string | null {
    // A form save must never silently rebuild the doc from a broken base —
    // refuse instead of dropping everything outside the form fields.
    const doc = plainObject(content);
    if (doc === null) return null;
    for (const [section, leaves] of FORM_FIELDS) {
      // create intermediate objects on demand so a missing section can be added
      let node: Record<string, unknown> = doc;
      for (const p of section.split(".")) {
        node[p] = (node[p] as Record<string, unknown> | undefined) ?? {};
        node = node[p] as Record<string, unknown>;
      }
      leaves.forEach((leaf) => {
        const v = formDraft[`${section}.${leaf}`];
        if (v !== undefined) node[leaf] = v;
      });
    }
    return yamlDump(doc);
  }

  function saveCurrent() {
    if (mode === "form") {
      const merged = applyFormEdits();
      if (merged === null) {
        message.error(t("settings.yamlUnparsable"));
        return;
      }
      save.mutate({ content: merged, expectedHash: diskHash });
      return;
    }
    save.mutate({ content, expectedHash: diskHash });
  }

  // Selects the reported line in the YAML editor (the textarea has no
  // gutter, so "line 96" alone cannot be found).
  function goToLine(line: number) {
    setMode("yaml");
    // after the mode switch has rendered the textarea
    requestAnimationFrame(() => {
      const ta = editor.current?.resizableTextArea?.textArea;
      if (!ta) return;
      const lines = ta.value.split("\n");
      const start = lines.slice(0, line - 1).reduce((n, l) => n + l.length + 1, 0);
      const end = start + (lines[line - 1]?.length ?? 0);
      ta.focus();
      ta.setSelectionRange(start, end);
      const lineHeight = parseFloat(getComputedStyle(ta).lineHeight) || 20;
      ta.scrollTop = Math.max(0, (line - 3) * lineHeight);
    });
  }

  const placeholderKeys = (env.data?.keys ?? []).filter((k) => k.is_placeholder).map((k) => k.key);
  const envColumns: TableProps<EnvKeyOut>["columns"] = [
    { title: t("settings.envKeyColumn"), dataIndex: "key" },
    {
      title: t("settings.envMaskedColumn"),
      render: (_, row) => row.is_placeholder
        ? <Tag color="warning">{t("settings.envNotSet")}</Tag>
        : <Text code>{row.masked}</Text>,
    },
    {
      title: "",
      render: (_, row) => (
        <Popconfirm
          title={t("settings.envDeleteConfirm", { key: row.key })}
          okText={t("common.delete")}
          okButtonProps={{ danger: true }}
          onConfirm={() => deleteEnv.mutate(row.key)}
          disabled={!writable}
        >
          <Button size="small" danger disabled={!writable}>{t("common.delete")}</Button>
        </Popconfirm>
      ),
    },
  ];

  if (settings.isPending) return <Text>{t("common.loading")}</Text>;
  if (settings.error) return <Alert type="error" showIcon message={settings.error.message} />;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      {frozen && <Alert type="warning" showIcon message={t("settings.frozenNotice")} />}

      {/* Keys first (R4-23): an index cannot run until the API key is set,
          and that step used to sit below an 18-row YAML box. */}
      <div>
        <Typography.Title level={5}>{t("settings.envTitle")}</Typography.Title>
        {placeholderKeys.length > 0 && (
          <Alert type="warning" showIcon style={{ marginBottom: 8 }}
                 message={t("settings.envPlaceholderAlert", { keys: placeholderKeys.join(", ") })} />
        )}
        <Table rowKey="key" size="small" columns={envColumns} dataSource={env.data?.keys ?? []}
               pagination={false} loading={env.isPending} />
        <Space.Compact style={{ marginTop: 8, width: "100%" }}>
          <Input placeholder={t("settings.envKeyPlaceholder")} value={envKey}
                 disabled={!writable} onChange={(e) => setEnvKey(e.target.value)} />
          <Input.Password placeholder={t("settings.envValuePlaceholder")} value={envValue}
                          disabled={!writable} onChange={(e) => setEnvValue(e.target.value)} />
          <Button type="primary" disabled={!writable || !envKey || !envValue}
                  loading={patchEnv.isPending} onClick={() => patchEnv.mutate()}>{t("settings.envSetButton")}</Button>
        </Space.Compact>
      </div>

      <div>
        <Space style={{ marginBottom: 8 }}>
          <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
            <Radio.Button value="yaml">{t("settings.yamlMode")}</Radio.Button>
            <Radio.Button value="form">{t("settings.formMode")}</Radio.Button>
          </Radio.Group>
          <Button type="primary" disabled={!writable || !dirty} loading={save.isPending}
                  onClick={saveCurrent}>{t("settings.saveSettings")}</Button>
          {dirty && <Tag color="processing">{t("settings.unsaved")}</Tag>}
        </Space>
        {saveError && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 8 }}
            message={saveError.line !== undefined
              ? t("settings.errorAt", { line: saveError.line, column: saveError.column })
              : saveError.text}
            description={saveError.line !== undefined ? saveError.text : undefined}
            action={saveError.line !== undefined && (
              <Button size="small" onClick={() => goToLine(saveError.line!)}>{t("settings.goToLine")}</Button>
            )}
          />
        )}
        {mode === "yaml" ? (
          <TextArea
            ref={editor}
            aria-label="settings-yaml"
            rows={18}
            style={{ fontFamily: "monospace" }}
            value={content}
            onChange={(e) => setContent(e.target.value)}
            disabled={!canEdit}
          />
        ) : (
          <Space direction="vertical" style={{ width: "100%" }}>
            {base === null && (
              <Alert type="warning" showIcon message={t("settings.yamlNotObject")} />
            )}
            {MODEL_SECTIONS.map((section) => (
              <Descriptions key={section} size="small" bordered column={1}
                            title={section.startsWith("completion")
                              ? t("settings.modelTitle") : t("settings.embeddingModelTitle")}>
                {COMPLETION_PATHS.map((leaf) => (
                  <Descriptions.Item key={leaf} label={leaf}>
                    <Input {...textField(`${section}.${leaf}`)} />
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ))}
            <Descriptions title={t("settings.chunkTitle")} size="small" bordered column={2}>
              <Descriptions.Item label="size"><InputNumber aria-label="chunk-size" {...numberField("chunking.size")} /></Descriptions.Item>
              <Descriptions.Item label="overlap"><InputNumber aria-label="chunk-overlap" {...numberField("chunking.overlap")} /></Descriptions.Item>
            </Descriptions>
            <Descriptions title={t("settings.inputTitle")} size="small" bordered column={2}>
              <Descriptions.Item label="type"><Text>{formValue("input.type")}</Text></Descriptions.Item>
              <Descriptions.Item label="file_pattern"><Text code>{formValue("input.file_pattern")}</Text></Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </div>

      <Modal
        open={conflict !== null}
        title={t("settings.conflictTitle")}
        onCancel={() => setConflict(null)}
        footer={[
          <Button key="reload" onClick={() => {
            if (!conflict) return;
            setContent(conflict.currentContent);
            setConflict(null);
            qc.invalidateQueries({ queryKey: projectSettings(projectId).queryKey });
          }}>{t("settings.reload")}</Button>,
          <Button key="overwrite" danger type="primary" onClick={() => {
            if (!conflict) return;
            save.mutate({ content: conflict.myContent, expectedHash: conflict.currentHash });
          }}>{t("settings.overwrite")}</Button>,
        ]}
      >
        <Space direction="vertical" style={{ width: "100%" }}>
          <Text strong>{t("settings.serverContent")}</Text>
          <pre style={{ background: "#fafafa", padding: 8 }}>{conflict?.currentContent}</pre>
          <Text strong>{t("settings.yourContent")}</Text>
          <pre style={{ background: "#fafafa", padding: 8 }}>{conflict?.myContent}</pre>
        </Space>
      </Modal>

      <Modal
        open={blocker.state === "blocked"}
        title={t("settings.leaveTitle")}
        okText={t("settings.leaveDiscard")}
        okButtonProps={{ danger: true }}
        cancelText={t("settings.leaveStay")}
        onOk={() => blocker.proceed?.()}
        onCancel={() => blocker.reset?.()}
      >
        {t("settings.leaveBody")}
      </Modal>

      <div>
        <Typography.Title level={5}>{t("settings.versionHistory")}</Typography.Title>
        {versions.data?.items.length === 0 && (
          <Text type="secondary">{t("settings.noVersions")}</Text>
        )}
        <Collapse items={(versions.data?.items ?? []).map((v) => ({
          key: v.id,
          label: <span>{formatDateTime(v.created_at, i18n.language)} · <Text code>{v.content_hash.slice(0, 8)}</Text></span>,
          children: (
            <Space>
              <Button size="small" onClick={async () => {
                const detail = await fetchVersion(v.id);
                if (detail) setViewVersion(detail);
              }}>{t("settings.view")}</Button>
              <Button size="small" disabled={!writable} onClick={async () => {
                const detail = await fetchVersion(v.id);
                if (detail) save.mutate({ content: detail.content, expectedHash: diskHash });
              }}>{t("settings.restore")}</Button>
            </Space>
          ),
        }))} />
        <Pagination
          size="small"
          style={{ marginTop: 8 }}
          current={versionsPage}
          pageSize={VERSIONS_PAGE_SIZE}
          total={versions.data?.total ?? 0}
          showSizeChanger={false}
          hideOnSinglePage
          onChange={setVersionsPage}
        />
      </div>

      <Modal open={viewVersion !== null} title={t("settings.versionTitle", { id: viewVersion?.id })} footer={<Button onClick={() => setViewVersion(null)}>{t("settings.close")}</Button>}>
        <pre style={{ background: "#fafafa", padding: 8, maxHeight: 400, overflow: "auto" }}>{viewVersion?.content}</pre>
      </Modal>

      <div>
        <Space style={{ marginBottom: 8 }}>
          <Button disabled={!canEdit} loading={dryRunMutation.isPending}
                  onClick={() => dryRunMutation.mutate()}>{t("settings.dryRunButton")}</Button>
        </Space>
        {dryRun && (
          <Alert
            type={dryRun.ok ? "success" : "error"}
            showIcon
            message={dryRun.ok ? t("settings.dryRunPassed") : t("settings.dryRunFailed")}
            description={
              <>
                {dryRun.ok && <div>{t("settings.dryRunScope")}</div>}
                {dryRun.output && (
                  <pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>{dryRun.output}</pre>
                )}
              </>
            }
          />
        )}
      </div>
    </Space>
  );
}
