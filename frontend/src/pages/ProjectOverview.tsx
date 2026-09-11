import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Alert, Card, Space, Spin, Typography } from "antd";
import { api } from "../api/client";
import type { ProjectHealth } from "../api/types";
import ActionCard from "../components/project/ActionCard";
import { jobTypeLabel } from "../components/project/nextAction";
import { methodLabel } from "../components/tests/methods";

// The knowledge-base health overview (spec §9.3): one action card that is
// one ordered check, four stat tiles, and two recent-activity mini-cards.
// Everything on this page comes from /health — the same query the sidebar
// badges use, so the two views can never disagree on the counts.
export default function ProjectOverview({ projectId }: { projectId: string }) {
  const { t } = useTranslation();

  // Identical key and queryFn to ProjectSidebar: one request feeds both.
  // A failed fetch resolves to null (not an error) so whichever observer
  // registers first, the overview's empty state is deterministic.
  const health = useQuery({
    queryKey: ["projects", projectId, "health"],
    queryFn: async () => {
      const r = await api(`/api/projects/${projectId}/health`);
      return r.ok ? ((await r.json()) as ProjectHealth) : null;
    },
    retry: false,
  });

  return (
    <Space direction="vertical" size="large" style={{ width: "100%" }}>
      <Typography.Title level={4} style={{ margin: 0 }}>
        {t("overview.heading")}
      </Typography.Title>

      {health.isPending ? (
        <Spin />
      ) : !health.data ? (
        <Alert type="warning" showIcon message={t("overview.loadFailed")} />
      ) : (
        <OverviewBody projectId={projectId} data={health.data} />
      )}
    </Space>
  );
}

function OverviewBody({ projectId, data }: { projectId: string; data: ProjectHealth }) {
  const { t, i18n } = useTranslation();
  const run = data.latest_run;
  const pending = data.files.new + data.files.modified;

  return (
    <>
      <ActionCard projectId={projectId} health={data} />

      <Space wrap size="middle">
        <Stat label={t("overview.statDocuments")} value={t("overview.countDocuments", { count: data.files.total })} />
        <Stat label={t("overview.statPending")} value={t("overview.countPending", { count: pending })} />
        <Stat
          label={t("overview.statLastIndex")}
          value={data.last_index
            ? new Date(data.last_index.finished_at).toLocaleString(i18n.language)
            : t("overview.neverIndexed")}
        />
        <Stat
          label={t("overview.statRatings")}
          value={run
            ? t("overview.ratingSummary", {
                good: run.ratings.good, fair: run.ratings.fair,
                poor: run.ratings.poor, unrated: run.ratings.unrated,
              })
            : t("overview.noRuns")}
        />
      </Space>

      <Space wrap size="middle" align="start">
        <Card size="small" title={t("overview.lastIndexTitle")} style={{ minWidth: 260 }}>
          {data.last_index ? (
            <Typography.Text>
              {t("overview.lastIndexLine", {
                type: jobTypeLabel(data.last_index.type, t),
                time: new Date(data.last_index.finished_at).toLocaleString(i18n.language),
              })}
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">{t("overview.neverIndexed")}</Typography.Text>
          )}
        </Card>
        <Card size="small" title={t("overview.latestRunTitle")} style={{ minWidth: 260 }}>
          {run ? (
            <Typography.Text>
              {t("overview.latestRunLine", {
                method: methodLabel(run.method, t),
                count: run.regressions,
              })}
            </Typography.Text>
          ) : (
            <Typography.Text type="secondary">{t("overview.noRuns")}</Typography.Text>
          )}
        </Card>
      </Space>
    </>
  );
}

// One stat tile: label above, value below. Counts render with their unit
// rather than a bare number, so they never collide with the sidebar's
// badge text in tests or screen readers.
// zh-TW: the count unit is 份, as the stat value renders "3 份".
function Stat({ label, value }: { label: string; value: string }) {
  return (
    <Card size="small" style={{ minWidth: 150 }}>
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Typography.Paragraph style={{ marginBottom: 0, fontSize: 18 }}>
        {value}
      </Typography.Paragraph>
    </Card>
  );
}
