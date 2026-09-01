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
import ProjectDetail from "./pages/ProjectDetail";
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
              <Route path="/projects/:id" element={<ProjectDetail />} />
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
