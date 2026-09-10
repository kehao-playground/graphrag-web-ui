import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Typography } from "antd";
import type { ProjectHealth } from "../../api/types";
import { jobTypeLabel, nextAction } from "./nextAction";
import type { NextActionKey } from "./nextAction";

// The rendered ordered check (spec §9.3): the ladder's pick, explained,
// with a link to its target that already carries the filters. When
// silent-skip detection is off, every card gains the caveat — skipped is
// not evidence either way, least of all on the healthy card.
export default function ActionCard({ projectId, health }: {
  projectId: string;
  health: ProjectHealth;
}) {
  const { t } = useTranslation();
  const a = nextAction(health);

  const copy: Record<NextActionKey, { title: string; desc: string; link: string | null }> = {
    activeJob: {
      title: t("overview.actionActiveJobTitle"),
      desc: t("overview.actionActiveJobDesc", {
        type: jobTypeLabel(health.active_job?.type ?? "", t),
      }),
      link: t("overview.linkJobs"),
    },
    artifactsMissing: {
      title: t("overview.actionArtifactsMissingTitle"),
      desc: t("overview.actionArtifactsMissingDesc"),
      link: t("overview.linkJobs"),
    },
    noBaseline: {
      title: t("overview.actionNoBaselineTitle"),
      desc: t("overview.actionNoBaselineDesc"),
      link: t("overview.linkJobs"),
    },
    removed: {
      title: t("overview.actionRemovedTitle"),
      desc: t("overview.actionRemovedDesc", { count: health.files.removed }),
      link: t("overview.linkJobs"),
    },
    stale: {
      title: t("overview.actionStaleTitle"),
      desc: t("overview.actionStaleDesc", { count: health.files.new + health.files.modified }),
      link: t("overview.linkPendingFiles"),
    },
    skipped: {
      title: t("overview.actionSkippedTitle"),
      desc: t("overview.actionSkippedDesc", { count: health.files.skipped }),
      link: t("overview.linkSkippedFiles"),
    },
    regressions: {
      title: t("overview.actionRegressionsTitle"),
      desc: t("overview.actionRegressionsDesc", {
        count: health.latest_run?.regressions ?? 0,
      }),
      link: t("overview.linkRegressions"),
    },
    healthy: {
      title: t("overview.actionHealthyTitle"),
      desc: t("overview.actionHealthyDesc"),
      link: null,
    },
  };

  const c = copy[a.key];
  return (
    <Alert
      type={a.severity}
      showIcon
      message={c.title}
      description={
        <>
          <Typography.Paragraph style={{ marginBottom: 8 }}>{c.desc}</Typography.Paragraph>
          {health.ingest_check === "unavailable_title_column" && (
            <Typography.Paragraph type="secondary" style={{ marginBottom: 8 }}>
              {t("overview.caveatTitleColumn")}
            </Typography.Paragraph>
          )}
          {a.target && c.link && (
            <Link to={`/projects/${projectId}/${a.target}`}>{c.link}</Link>
          )}
        </>
      }
    />
  );
}
