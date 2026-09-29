import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Layout as AntLayout, Menu, Select, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";
import ChangePasswordModal from "./ChangePasswordModal";
import ErrorBoundary from "./ErrorBoundary";

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout, authMode } = useAuth();
  const { t, i18n } = useTranslation();
  const [changingPassword, setChangingPassword] = useState(false);

  useEffect(() => {
    document.documentElement.lang = i18n.language;
    document.title = t("layout.title");
  }, [i18n.language, t]);

  // The backend-computed atom is the only gate (spec §8): any holder of
  // users:manage — built-in user_admin, a custom role, ops via act_any —
  // sees the admin entry; the frontend never maps role names to rights.
  const canManageUsers = !!user?.permissions?.includes("users:manage");
  const items = [
    { key: "/projects", label: t("layout.projects") },
    ...(canManageUsers ? [
      { key: "/admin/users", label: t("layout.adminUsers") },
      { key: "/admin/roles", label: t("layout.adminRoles") },
      { key: "/admin/audit", label: t("layout.adminAudit") },
    ] : []),
  ];

  // zh-TW: /projects/:id must also highlight the 專案 (projects) nav item; same for the /admin prefixes
  const selectedKey = location.pathname.startsWith("/projects") ? "/projects"
    : location.pathname.startsWith("/admin/roles") ? "/admin/roles"
    : location.pathname.startsWith("/admin/audit") ? "/admin/audit"
    : location.pathname.startsWith("/admin") ? "/admin/users"
    : location.pathname;

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <AntLayout.Sider>
        {/* antd's .ant-layout-sider-children is display:block — auto
            margins push nothing. A flex column wrapper is what actually
            pins the language dropdown to the bottom-left corner. */}
        <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
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
          items={[
            // The proxy IdP owns passwords (spec §6.3); local accounts
            // change their own here (RBAC §4.1 baseline, R3-09).
            ...(authMode === "proxy" ? [] : [{ key: "password", label: t("layout.changePassword") }]),
            { key: "logout", label: t("layout.logout") },
          ]}
          onClick={({ key }) => {
            if (key === "password") setChangingPassword(true);
            else logout().catch(() => {}).finally(() => navigate("/login"));
          }}
        />
        {/* Language dropdown pinned to the very bottom-left corner, with
            the free space between logout and it absorbing the stretch.
            Option values are the i18n language codes themselves. */}
        <div style={{ marginTop: "auto", padding: "0 16px 16px" }}>
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
        </div>
      </AntLayout.Sider>
      <AntLayout.Content style={{ padding: 24 }}>
        <Typography.Text type="secondary">{user?.display_name}({user?.email})</Typography.Text>
        {/* A crashed pane stays inside the content area; navigating
            elsewhere clears it. */}
        <ErrorBoundary resetKey={location.pathname}>
          <Outlet />
        </ErrorBoundary>
      </AntLayout.Content>
      <ChangePasswordModal
        open={changingPassword}
        title={t("login.selfChangeTitle")}
        onCancel={() => setChangingPassword(false)}
        onDone={() => setChangingPassword(false)}
        onSignedOut={() => { setChangingPassword(false); navigate("/login"); }}
      />
    </AntLayout>
  );
}
