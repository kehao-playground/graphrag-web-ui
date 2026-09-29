import type { ReactNode } from "react";
import { useAuth } from "../stores/auth";
import Forbidden from "./Forbidden";

// Route guard on a global atom (R4-37). The nav already hides what the
// atoms do not reach; this covers a typed or shared URL, so the page never
// mounts to fire requests the server will refuse. The server stays the
// authority — this only decides what to render.
export default function RequirePermission({ atom, children }: { atom: string; children: ReactNode }) {
  const allowed = useAuth((s) => !!s.user?.permissions?.includes(atom));
  return allowed ? <>{children}</> : <Forbidden />;
}
