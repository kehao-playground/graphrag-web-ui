import { useTranslation } from "react-i18next";
import { Input, Progress, Select, Typography } from "antd";
import type { TagEntry } from "../../api/types";
import { INDEX_STATES, humanBytes } from "./indexState";
import type { IndexState } from "./indexState";

// Client-side filter controls (spec §9.1): filename search, tag multi-select
// and index-state multi-select on the left, the quota bar on the right edge
// where it moved from its own block between the uploader and the table.
export default function FilesToolbar({ search, onSearch, tags, selectedTags, onTags, state, onState, usageBytes, quotaBytes }: {
  search: string;
  onSearch: (v: string) => void;
  tags: TagEntry[];
  selectedTags: string[];
  onTags: (v: string[]) => void;
  state: IndexState[];
  onState: (v: IndexState[]) => void;
  usageBytes: number;
  quotaBytes: number;
}) {
  const { t } = useTranslation();
  const percent = quotaBytes > 0 ? Math.round((usageBytes / quotaBytes) * 100) : 0;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
      <Input
        allowClear
        placeholder={t("files.searchPlaceholder")}
        style={{ width: 200 }}
        value={search}
        onChange={(e) => onSearch(e.target.value)}
      />
      <Select
        mode="multiple"
        allowClear
        placeholder={t("files.tags")}
        style={{ minWidth: 160, flex: "1 1 160px", maxWidth: 280 }}
        maxTagCount="responsive"
        value={selectedTags}
        options={tags.map((tag) => ({ label: tag.name, value: tag.name }))}
        onChange={onTags}
      />
      <Select
        mode="multiple"
        allowClear
        placeholder={t("files.indexState")}
        style={{ minWidth: 160, flex: "1 1 160px", maxWidth: 280 }}
        maxTagCount="responsive"
        value={state}
        options={INDEX_STATES.map((s) => ({ label: t(`files.state.${s}`), value: s }))}
        onChange={onState}
      />
      <div style={{ marginLeft: "auto", minWidth: 220, maxWidth: 360 }}>
        <Typography.Text type="secondary">{t("files.usage", { used: humanBytes(usageBytes), quota: humanBytes(quotaBytes) })}</Typography.Text>
        {/* explicit format keeps the percent visible in exception status (antd
            swaps the text for an icon when only status is set) */}
        <Progress percent={percent} status={percent > 90 ? "exception" : "normal"} format={(p) => `${p}%`} />
      </div>
    </div>
  );
}
