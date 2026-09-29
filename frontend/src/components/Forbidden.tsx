import { Button, Result } from "antd";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";

// The one "no access" page (R4-37): a route the user's atoms do not reach,
// or a project the server refused. It always offers the way back.
export default function Forbidden({ notFound = false }: { notFound?: boolean }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  return (
    <Result
      status={notFound ? "404" : "403"}
      title={notFound ? t("forbidden.notFoundTitle") : t("forbidden.title")}
      subTitle={t("forbidden.subtitle")}
      extra={<Button type="primary" onClick={() => navigate("/projects")}>{t("forbidden.back")}</Button>}
    />
  );
}
