import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";
import antdEnUS from "antd/locale/en_US";
import antdZhTW from "antd/locale/zh_TW";
import { useTranslation } from "react-i18next";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import ProjectDetail, { ProjectPane } from "./pages/ProjectDetail";
import AdminUsers from "./pages/AdminUsers";
import AdminRoles from "./pages/AdminRoles";
import AdminAudit from "./pages/AdminAudit";
import { useAuth } from "./stores/auth";
import "./i18n";

const queryClient = new QueryClient();

export default function App() {
  const { i18n } = useTranslation();
  useEffect(() => { useAuth.getState().restore(); }, []);

  return (
    <ConfigProvider locale={i18n.language === "zh-TW" ? antdZhTW : antdEnUS}>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
              <Route path="/" element={<Navigate to="/projects" replace />} />
              <Route path="/projects" element={<Projects />} />
              {/* Panes are routes, not tab state (spec §4): reloads stay on
                  their pane, every view is a shareable link, and the slice ③
                  overview can deep-link files with ?state= pre-applied. */}
              <Route path="/projects/:id" element={<ProjectDetail />}>
                <Route index element={<Navigate to="overview" replace />} />
                <Route path="overview" element={<ProjectPane pane="overview" />} />
                <Route path="files" element={<ProjectPane pane="files" />} />
                <Route path="jobs" element={<ProjectPane pane="jobs" />} />
                <Route path="tests" element={<ProjectPane pane="tests" />} />
                <Route path="explore" element={<ProjectPane pane="explore" />} />
                <Route path="settings" element={<ProjectPane pane="settings" />} />
                <Route path="members" element={<ProjectPane pane="members" />} />
              </Route>
              <Route path="/admin/users" element={<AdminUsers />} />
              <Route path="/admin/roles" element={<AdminRoles />} />
              <Route path="/admin/audit" element={<AdminAudit />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </QueryClientProvider>
    </ConfigProvider>
  );
}
