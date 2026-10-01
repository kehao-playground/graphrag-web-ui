import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, Collapse, Modal, Pagination, Space, Typography, message } from "antd";
import { apiJson } from "../../api/client";
import { settingsVersions } from "../../api/queries";
import type { SettingsVersionDetail } from "../../api/types";
import { formatDateTime } from "../../i18n/format";

const { Text } = Typography;

// Versions per page of the history; the server pages it (R3-10).
const VERSIONS_PAGE_SIZE = 20;

// The saved settings.yaml versions: view one, or hand its content to
// onRestore (the pane's save, so a restore goes through the conflict check).
export default function VersionHistory({ projectId, writable, onRestore }: {
  projectId: string;
  writable: boolean;
  onRestore: (content: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [page, setPage] = useState(1);
  const [viewVersion, setViewVersion] = useState<SettingsVersionDetail | null>(null);
  const versions = useQuery(settingsVersions(projectId, {
    limit: VERSIONS_PAGE_SIZE, offset: (page - 1) * VERSIONS_PAGE_SIZE,
  }));

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

  return (
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
              if (detail) onRestore(detail.content);
            }}>{t("settings.restore")}</Button>
          </Space>
        ),
      }))} />
      <Pagination
        size="small"
        style={{ marginTop: 8 }}
        current={page}
        pageSize={VERSIONS_PAGE_SIZE}
        total={versions.data?.total ?? 0}
        showSizeChanger={false}
        hideOnSinglePage
        onChange={setPage}
      />
      <Modal open={viewVersion !== null} title={t("settings.versionTitle", { id: viewVersion?.id })} footer={<Button onClick={() => setViewVersion(null)}>{t("settings.close")}</Button>}>
        <pre style={{ background: "#fafafa", padding: 8, maxHeight: 400, overflow: "auto" }}>{viewVersion?.content}</pre>
      </Modal>
    </div>
  );
}
