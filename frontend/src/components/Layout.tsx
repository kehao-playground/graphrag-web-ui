import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Layout as AntLayout, Menu, Typography } from "antd";
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
          selectedKeys={[i18n.language]}
          onClick={({ key }) => { void i18n.changeLanguage(key); }}
          items={[{
            key: "language", type: "group", label: t("layout.language"),
            children: [
              { key: "zh-TW", label: "中文" },
              { key: "en-US", label: "English" },
            ],
          }]}
          // marginTop:auto pushes the language menu (and the logout menu
          // below it) to the Sider bottom; the logout Menu DROPS its own
          // marginTop:"auto" — two auto margins would split the free space
          // and park the switcher mid-column instead of above logout.
          // The language keys double as i18n language codes.
          style={{ marginTop: "auto" }}
        />
        <Menu
          theme="dark"
          mode="inline"
          selectable={false}
          items={[{ key: "logout", label: t("layout.logout") }]}
          onClick={() => { logout().catch(() => {}).finally(() => navigate("/login")); }}
        />
      </AntLayout.Sider>
      <AntLayout.Content style={{ padding: 24 }}>
        <Typography.Text type="secondary">{user?.display_name}({user?.email})</Typography.Text>
        <Outlet />
      </AntLayout.Content>
    </AntLayout>
  );
}
