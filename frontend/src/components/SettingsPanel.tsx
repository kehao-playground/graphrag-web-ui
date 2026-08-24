import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { dump as yamlDump, load as yamlLoad } from "js-yaml";
import {
  Alert, Button, Collapse, Descriptions, Input, Modal, Radio, Space, Table, Typography, message,
} from "antd";
import type { TableProps } from "antd";
import { api, bodyOf, messageOfBody } from "../api/client";
import type {
  EnvKeyOut, SettingsOut, SettingsVersionDetail, SettingsVersionOut,
} from "../api/types";

const { TextArea } = Input;
const { Text } = Typography;

interface Conflict {
  currentContent: string;
  currentHash: string;
  myContent: string;
}


// Form mode edits these paths in the parsed document (spec §6.5: input.* is
// locked at creation and shown read-only).
const COMPLETION_PATHS = ["model", "model_provider", "auth_method"] as const;

export default function SettingsPanel({ projectId, canEdit }: {
  projectId: string;
  canEdit: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [mode, setMode] = useState<"yaml" | "form">("yaml");
  const [conflict, setConflict] = useState<Conflict | null>(null);
  const [viewVersion, setViewVersion] = useState<SettingsVersionDetail | null>(null);
  const [dryRun, setDryRun] = useState<{ ok: boolean; output: string } | null>(null);
  const [envKey, setEnvKey] = useState("");
  const [envValue, setEnvValue] = useState("");

  const settings = useQuery({
    queryKey: ["projects", projectId, "settings"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/settings`);
      if (!r.ok) throw new Error(messageOfBody(await bodyOf(r), "settings.loadFailed"));
      return (await r.json()) as SettingsOut;
    },
  });

  // Local edit keyed by the server hash it was based on: while the hash
  // matches, the local text wins; a new server hash (successful save,
  // reload, restore) resyncs automatically — derived during render.
  const [edit, setEdit] = useState<{ hash: string; content: string } | null>(null);
  const content = edit && edit.hash === settings.data?.content_hash
    ? edit.content
    : (settings.data?.content ?? "");
  const setContent = (c: string) => setEdit({ hash: settings.data?.content_hash ?? "", content: c });
  const versions = useQuery({
    queryKey: ["projects", projectId, "versions"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/settings/versions`);
      if (!r.ok) throw new Error(t("settings.loadVersionsFailed"));
      return (await r.json()) as SettingsVersionOut[];
    },
  });

  const env = useQuery({
    queryKey: ["projects", projectId, "env"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/env`);
      if (!r.ok) throw new Error(t("settings.loadEnvFailed"));
      return (await r.json()) as { keys: EnvKeyOut[] };
    },
  });

  useEffect(() => {
    if (settings.error) message.error(settings.error.message);
  }, [settings.error]);

  async function fetchVersion(id: number): Promise<SettingsVersionDetail | null> {
    const r = await api(`/api/projects/${projectId}/settings/versions/${id}`);
    if (!r.ok) {
      message.error(t("settings.loadVersionFailed"));
      return null;
    }
    return (await r.json()) as SettingsVersionDetail;
  }

  // Shared PUT: 409 stores the server-side state for the conflict modal
  // (both direct saves and restores flow through here).
  const save = useMutation({
    mutationFn: async ({ content: c, expectedHash }: { content: string; expectedHash: string }) => {
      const r = await api(`/api/projects/${projectId}/settings`, {
        method: "PUT",
        body: JSON.stringify({ content: c, expected_hash: expectedHash }),
      });
      return { r, c };
    },
    onSuccess: async ({ r, c }) => {
      if (r.status === 409) {
        const body = await bodyOf(r);
        setConflict({
          currentContent: String(body.current_content ?? ""),
          currentHash: String(body.current_hash ?? ""),
          myContent: c,
        });
        return;
      }
      if (!r.ok) {
        message.error(messageOfBody(await bodyOf(r), "settings.saveFailed"));
        return;
      }
      setConflict(null);
      message.success(t("settings.saved"));
      await qc.invalidateQueries({ queryKey: ["projects", projectId, "settings"] });
      await qc.invalidateQueries({ queryKey: ["projects", projectId, "versions"] });
    },
  });

  const dryRunMutation = useMutation({
    mutationFn: async () => {
      const r = await api(`/api/projects/${projectId}/dry-run`, { method: "POST" });
      return { r };
    },
    onSuccess: async ({ r }) => {
      const body = await bodyOf(r);
      if (r.ok) {
        setDryRun({ ok: Boolean((body as { ok?: boolean }).ok), output: String(body.output ?? "") });
      } else {
        setDryRun({ ok: false, output: messageOfBody(body, "settings.dryRunFailed") });
      }
    },
  });

  const patchEnv = useMutation({
    mutationFn: async () => {
      const r = await api(`/api/projects/${projectId}/env`, {
        method: "PATCH",
        body: JSON.stringify({ key: envKey, value: envValue }),
      });
      return { r };
    },
    onSuccess: async ({ r }) => {
      if (!r.ok) {
        message.error(messageOfBody(await bodyOf(r), "settings.envSetFailed"));
        return;
      }
      setEnvKey("");
      setEnvValue("");
      message.success(t("settings.envSet"));
      await qc.invalidateQueries({ queryKey: ["projects", projectId, "env"] });
    },
  });

  const deleteEnv = useMutation({
    mutationFn: async (key: string) => {
      const r = await api(`/api/projects/${projectId}/env/${encodeURIComponent(key)}`, { method: "DELETE" });
      return { r };
    },
    onSuccess: async ({ r }) => {
      if (!r.ok) {
        message.error(t("settings.envDeleteFailed"));
        return;
      }
      message.success(t("settings.envDeleted"));
      await qc.invalidateQueries({ queryKey: ["projects", projectId, "env"] });
    },
  });
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
  const base = mode === "form" ? plainObject(content) : null;
  const parsed = base ?? {};
  const diskHash = settings.data?.content_hash ?? "";

  function formValue(section: string, leaf: string): string {
    const s = parsed[section] as Record<string, unknown> | undefined;
    return String((s as Record<string, unknown> | undefined)?.[leaf] ?? "");
  }

  // Draft keyed by the server hash it was typed against (same pattern as the
  // YAML editor): a new server hash (save, reload, restore, concurrent edit)
  // discards the draft so stale values cannot be written back.
  const [draft, setDraft] = useState<{ hash: string; values: Record<string, string> }>({ hash: "", values: {} });
  const formDraft = draft.hash === diskHash ? draft.values : {};
  const setFormDraft = (updater: (d: Record<string, string>) => Record<string, string>) =>
    setDraft({ hash: diskHash, values: updater(formDraft) });
  const formField = (section: string, leaf: string) => ({
    value: formDraft[`${section}.${leaf}`] ?? formValue(section, leaf),
    onChange: (e: { target: { value: string } }) =>
      setFormDraft((d) => ({ ...d, [`${section}.${leaf}`]: e.target.value })),
    disabled: !canEdit,
  });

  function applyFormEdits(): string | null {
    // A form save must never silently rebuild the doc from a broken base —
    // refuse instead of dropping everything outside the form fields.
    const doc = plainObject(content);
    if (doc === null) return null;
    const sections: Array<[string, string[]]> = [
      ["completion_models.default_completion_model", [...COMPLETION_PATHS]],
      ["embedding_models.default_embedding_model", [...COMPLETION_PATHS]],
      ["chunking", ["size", "overlap"]],
    ];
    for (const [path, leaves] of sections) {
      const parts = path.split(".");
      // create intermediate objects on demand so a missing section can be added
      let node: Record<string, unknown> = doc;
      for (const p of parts) {
        node[p] = (node[p] as Record<string, unknown> | undefined) ?? {};
        node = node[p] as Record<string, unknown>;
      }
      leaves.forEach((leaf) => {
        const v = formDraft[`${parts.join(".")}.${leaf}`];
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

  const envColumns: TableProps<EnvKeyOut>["columns"] = [
    { title: "key", dataIndex: "key" },
    { title: "masked", dataIndex: "masked" },
    {
      title: "",
      render: (_, row) => (
        <Button size="small" danger disabled={!canEdit} onClick={() => deleteEnv.mutate(row.key)}>{t("common.delete")}</Button>
      ),
    },
  ];

  if (settings.isPending) return <Text>{t("common.loading")}</Text>;
  if (settings.error) return <Alert type="error" showIcon message={settings.error.message} />;

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <div>
        <Space style={{ marginBottom: 8 }}>
          <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)}>
            <Radio.Button value="yaml">YAML</Radio.Button>
            <Radio.Button value="form">Form</Radio.Button>
          </Radio.Group>
          <Button type="primary" disabled={!canEdit} loading={save.isPending} onClick={saveCurrent}>{t("settings.saveSettings")}</Button>
        </Space>
        {mode === "yaml" ? (
          <TextArea
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
            {[
              ["completion_models.default_completion_model", t("settings.modelTitle")],
              ["embedding_models.default_embedding_model", t("settings.embeddingModelTitle")],
            ].map(([section, label]) => (
              <Descriptions key={section} title={label} size="small" bordered column={1}>
                {COMPLETION_PATHS.map((leaf) => (
                  <Descriptions.Item key={leaf} label={leaf}>
                    <Input {...formField(section, leaf)} />
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ))}
            <Descriptions title="chunking" size="small" bordered column={2}>
              <Descriptions.Item label="size"><Input aria-label="chunk-size" {...formField("chunking", "size")} /></Descriptions.Item>
              <Descriptions.Item label="overlap"><Input aria-label="chunk-overlap" {...formField("chunking", "overlap")} /></Descriptions.Item>
            </Descriptions>
            <Descriptions title={t("settings.inputTitle")} size="small" bordered column={2}>
              <Descriptions.Item label="type"><Text>{formValue("input", "type")}</Text></Descriptions.Item>
              <Descriptions.Item label="file_pattern"><Text code>{formValue("input", "file_pattern")}</Text></Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </div>

      <Modal
        open={conflict !== null}
        title={t("settings.conflictTitle")}
        footer={[
          <Button key="reload" onClick={() => {
            if (!conflict) return;
            setContent(conflict.currentContent);
            setConflict(null);
            qc.invalidateQueries({ queryKey: ["projects", projectId, "settings"] });
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

      <div>
        <Typography.Title level={5}>{t("settings.versionHistory")}</Typography.Title>
        <Collapse items={(versions.data ?? []).map((v) => ({
          key: v.id,
          label: <span>{v.created_at} · <Text code>{v.content_hash.slice(0, 8)}</Text></span>,
          children: (
            <Space>
              <Button size="small" onClick={async () => {
                const detail = await fetchVersion(v.id);
                if (detail) setViewVersion(detail);
              }}>{t("settings.view")}</Button>
              <Button size="small" disabled={!canEdit} onClick={async () => {
                const detail = await fetchVersion(v.id);
                if (detail) save.mutate({ content: detail.content, expectedHash: diskHash });
              }}>{t("settings.restore")}</Button>
            </Space>
          ),
        }))} />
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
            description={<pre style={{ margin: 0, maxHeight: 240, overflow: "auto" }}>{dryRun.output}</pre>}
          />
        )}
      </div>

      <div>
        <Typography.Title level={5}>{t("settings.envTitle")}</Typography.Title>
        <Table rowKey="key" size="small" columns={envColumns} dataSource={env.data?.keys ?? []}
               pagination={false} loading={env.isPending} />
        <Space.Compact style={{ marginTop: 8, width: "100%" }}>
          <Input placeholder="key (e.g. GRAPHRAG_API_KEY)" value={envKey}
                 disabled={!canEdit} onChange={(e) => setEnvKey(e.target.value)} />
          <Input.Password placeholder="value" value={envValue}
                          disabled={!canEdit} onChange={(e) => setEnvValue(e.target.value)} />
          <Button type="primary" disabled={!canEdit || !envKey || !envValue}
                  loading={patchEnv.isPending} onClick={() => patchEnv.mutate()}>{t("settings.envSetButton")}</Button>
        </Space.Compact>
      </div>
    </Space>
  );
}
