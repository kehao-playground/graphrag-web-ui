import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useBlocker } from "react-router-dom";
import { Alert, Button, Input, Modal, Radio, Space, Tag, Typography, message } from "antd";
import type { TextAreaRef } from "antd/es/input/TextArea";
import { ApiRequestError, sendOk } from "../api/client";
import { jobsPreflight, projectSettings, settingsVersionsKey } from "../api/queries";
import type { SettingsConflict } from "../api/types";
import { isFrozen } from "./project/frozen";
import DryRunSection from "./settings/DryRunSection";
import EnvKeysSection from "./settings/EnvKeysSection";
import SettingsForm from "./settings/SettingsForm";
import VersionHistory from "./settings/VersionHistory";
import { formValueAt, mergeFormEdits, plainObject, yamlProblem } from "./settings/settingsYaml";
import type { FormDraft, FormValue, SaveError } from "./settings/settingsYaml";

const { TextArea } = Input;
const { Text } = Typography;

interface Conflict {
  currentContent: string;
  currentHash: string;
  myContent: string;
}

// The settings pane: .env keys, the settings.yaml editor (YAML or form mode)
// with its conflict and leave guards, version history and the dry run.
// Sections live in ./settings; this component owns the editor state and
// the one save every write (direct, form, restore, overwrite) goes through.
export default function SettingsPanel({ projectId, canEdit }: {
  projectId: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [mode, setMode] = useState<"yaml" | "form">("yaml");
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [saveError, setSaveError] = useState<SaveError | null>(null);
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

  // Draft keyed by the server hash it was typed against (same pattern as the
  // YAML editor): a new server hash (save, reload, restore, concurrent edit)
  // discards the draft so stale values cannot be written back.
  const [draft, setDraft] = useState<{ hash: string; values: FormDraft }>({ hash: "", values: {} });
  const formDraft = draft.hash === diskHash ? draft.values : {};
  // Parsed only when something reads it — the form view, or a form draft
  // the dirty check compares against — and once per content change, not
  // per keystroke in a form field (R1-115).
  const needsBase = mode === "form" || Object.keys(formDraft).length > 0;
  const base = useMemo(() => (needsBase ? plainObject(content) : null), [needsBase, content]);
  const formDirty = Object.entries(formDraft).some(([path, v]) => v !== formValueAt(base ?? {}, path));
  const dirty = content !== serverContent || formDirty;
  const setFormDraft = (path: string, v: FormValue | null) => {
    const values = { ...formDraft };
    if (v === null) delete values[path];
    else values[path] = v;
    setDraft({ hash: diskHash, values });
  };

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

  function saveCurrent() {
    if (mode === "form") {
      const merged = mergeFormEdits(content, formDraft);
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

  if (settings.isPending) return <Text>{t("common.loading")}</Text>;
  if (settings.error) return <Alert type="error" showIcon message={settings.error.message} />;

  return (
    <Space orientation="vertical" size="large" style={{ width: "100%" }}>
      {frozen && <Alert type="warning" showIcon message={t("settings.frozenNotice")} />}

      {/* Keys first (R4-23): an index cannot run until the API key is set,
          and that step used to sit below an 18-row YAML box. */}
      <EnvKeysSection projectId={projectId} writable={writable} />

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
          <SettingsForm base={base} draft={formDraft} onChange={setFormDraft} writable={writable} />
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
        <Space orientation="vertical" style={{ width: "100%" }}>
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

      <VersionHistory projectId={projectId} writable={writable}
                      onRestore={(c) => save.mutate({ content: c, expectedHash: diskHash })} />

      <DryRunSection projectId={projectId} canEdit={canEdit} />
    </Space>
  );
}
