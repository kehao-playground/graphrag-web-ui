import { Spin } from "antd";
import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../stores/auth";

export default function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, bootstrapping } = useAuth();
  if (bootstrapping) return <Spin style={{ display: "block", margin: "20vh auto" }} />;
  if (!user) return <Navigate to="/login" />;
  return <>{children}</>;
}
