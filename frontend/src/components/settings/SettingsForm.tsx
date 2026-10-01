import { useTranslation } from "react-i18next";
import { Alert, Descriptions, Input, InputNumber, Space, Typography } from "antd";
import { COMPLETION_PATHS, MODEL_SECTIONS, formValueAt } from "./settingsYaml";
import type { FormDraft, FormValue } from "./settingsYaml";

const { Text } = Typography;

// Form mode: the model blocks and chunk sizes as fields over the parsed
// document, input.* read-only (spec §6.5). base is null when the YAML is
// not a map; the fields then render empty under a degraded notice.
export default function SettingsForm({ base, draft, onChange, writable }: {
  base: Record<string, unknown> | null;
  draft: FormDraft;
  onChange: (path: string, value: FormValue | null) => void;
  writable: boolean;
}) {
  const { t } = useTranslation();
  const shown = (path: string) => draft[path] ?? formValueAt(base ?? {}, path);
  const textField = (path: string) => ({
    value: String(shown(path)),
    onChange: (e: { target: { value: string } }) => onChange(path, e.target.value),
    disabled: !writable,
  });
  // Chunk sizes are integers in graphrag's schema: an InputNumber keeps
  // them numbers, so the saved YAML says `size: 900`, not `size: '900'`.
  const numberField = (path: string) => {
    const v = shown(path);
    return {
      value: v === "" ? null : Number(v),
      onChange: (n: number | null) => onChange(path, n),
      precision: 0,
      min: 0,
      disabled: !writable,
      style: { width: "100%" },
    };
  };

  return (
    <Space orientation="vertical" style={{ width: "100%" }}>
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
        <Descriptions.Item label="type"><Text>{shown("input.type")}</Text></Descriptions.Item>
        <Descriptions.Item label="file_pattern"><Text code>{shown("input.file_pattern")}</Text></Descriptions.Item>
      </Descriptions>
    </Space>
  );
}
