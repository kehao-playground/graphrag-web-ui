import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Button, Input, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import type { TableProps } from "antd";
import { sendOk } from "../../api/client";
import { projectEnv } from "../../api/queries";
import type { EnvKeyOut } from "../../api/types";

const { Text } = Typography;

// The project's .env keys: masked values, the init placeholder called out
// (R4-23), set and delete. Sits first in the pane — an index cannot run
// until the API key is set.
export default function EnvKeysSection({ projectId, writable }: {
  projectId: string;
  writable: boolean;
}) {
  const qc = useQueryClient();
  const { t } = useTranslation();
  const [envKey, setEnvKey] = useState("");
  const [envValue, setEnvValue] = useState("");
  const env = useQuery(projectEnv(projectId));
  const invalidateEnv = () => qc.invalidateQueries({ queryKey: projectEnv(projectId).queryKey });

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

  const placeholderKeys = (env.data?.keys ?? []).filter((k) => k.is_placeholder).map((k) => k.key);
  const columns: TableProps<EnvKeyOut>["columns"] = [
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

  return (
    <div>
      <Typography.Title level={5}>{t("settings.envTitle")}</Typography.Title>
      {placeholderKeys.length > 0 && (
        <Alert type="warning" showIcon style={{ marginBottom: 8 }}
               message={t("settings.envPlaceholderAlert", { keys: placeholderKeys.join(", ") })} />
      )}
      <Table rowKey="key" size="small" columns={columns} dataSource={env.data?.keys ?? []}
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
  );
}
