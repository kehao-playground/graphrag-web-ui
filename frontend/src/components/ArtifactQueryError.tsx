import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Alert, Empty } from "antd";
import { ApiRequestError } from "../api/client";

// In-place error for the Explore reads (table list and graph). Their queries
// are meta.silent, so this is the only place the failure shows. A project
// without an index is the common case, not an error: it gets the empty state
// with the next step (R4-15); anything else is an inline alert.
export default function ArtifactQueryError({ error, projectId }: { error: Error; projectId: string }) {
  const { t } = useTranslation();
  if (error instanceof ApiRequestError && error.body.code === "not_indexed") {
    return (
      <Empty description={error.message}>
        <Link to={`/projects/${projectId}/jobs`}>{t("explore.goToJobs")}</Link>
      </Empty>
    );
  }
  return <Alert type="error" showIcon message={error.message} />;
}
