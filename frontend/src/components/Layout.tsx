import { Layout as AntLayout, Menu, Typography } from "antd";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth } from "../stores/auth";

export default function Layout() {
  const navigate = useNavigate();
  const location = useLocation();
  const { user, logout } = useAuth();

  const items = [
    { key: "/projects", label: "專案" },
  ...(user?.role === "admin" ? [{ key: "/admin/users", label: "管理者 — 使用者" }] : []),
  ];

  // /projects/:id 也要亮「專案」;admin 前綴同理
  const selectedKey = location.pathname.startsWith("/projects") ? "/projects"
    : location.pathname.startsWith("/admin") ? "/admin/users"
    : location.pathname;

  return (
    <AntLayout style={{ minHeight: "100vh" }}>
      <AntLayout.Sider>
        <div style={{ color: "#fff", padding: 16, fontWeight: 600 }}>GraphRAG Web UI</div>
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
          style={{ marginTop: "auto" }}
          items={[{ key: "logout", label: "登出" }]}
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
