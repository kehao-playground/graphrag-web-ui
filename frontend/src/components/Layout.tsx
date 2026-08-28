import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Layout as AntLayout, Menu, Select, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();
  const { t, i18n } = useTranslation();

  useEffect(() => {
    document.documentElement.lang = i18n.language;
    document.title = t("layout.title");
  }, [i18n.language, t]);

  const items = [
    { key: "/projects", label: t("layout.projects") },
  ...(user?.role === "admin" ? [{ key: "/admin/users", label: t("layout.adminUsers") }] : []),
  ];

  // zh-TW: /projects/:id must also highlight the 專案 (projects) nav item; same for the /admin prefix
  const selectedKey = location.pathname.startsWith("/projects") ? "/projects"
    : location.pathname.startsWith("/admin") ? "/admin/users"
    : location.pathname;

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <AntLayout.Sider>
        <div style={{ color: "#fff", padding: 16, fontWeight: 600 }}>{t("common.appName")}</div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
        <Menu
          theme="dark"
          mode="inline"
          selectable={false}
          items={[{ key: "logout", label: t("layout.logout") }]}
          onClick={() => { logout().catch(() => {}).finally(() => navigate("/login")); }}
          // marginTop:auto pushes logout (and the language select below it)
          // to the Sider bottom; the language select must NOT add its own
          // auto margin — two autos would split the free space and park
          // logout mid-column.
          style={{ marginTop: "auto" }}
        />
        {/* Language dropdown at the very bottom-left corner. Option values
            are the i18n language codes themselves. */}
        <div style={{ padding: "0 16px 16px" }}>
          <Select
            aria-label={t("layout.language")}
            value={i18n.language}
            onChange={(v) => { void i18n.changeLanguage(v); }}
            options={[
              { value: "zh-TW", label: "中文" },
              { value: "en-US", label: "English" },
            ]}
            popupMatchSelectWidth={false}
            style={{ width: "100%" }}
          />
        </div>
      </AntLayout.Sider>
      <AntLayout.Content style={{ padding: 24 }}>
        <Typography.Text type="secondary">{user?.display_name}({user?.email})</Typography.Text>
        <Outlet />
      </AntLayout.Content>
    </AntLayout>
  );
}
