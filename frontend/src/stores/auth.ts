import { create } from "zustand";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  bootstrapping: boolean;          // 重整後恢復 session 期間為 true
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<string | null>;
  restore: () => Promise<void>;
}
const REFRESH_KEY = "grui_refresh";

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  accessToken: null,
  bootstrapping: true,
  login: async (email, password) => {
    const r = await fetch("/api/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) return false;
    const body = await r.json();
    localStorage.setItem(REFRESH_KEY, body.refresh_token);
    set({ user: body.user, accessToken: body.access_token });
    return true;
  },
  logout: async () => {
    const t = localStorage.getItem(REFRESH_KEY);
    if (t) await fetch("/api/auth/logout", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: t }) });
    localStorage.removeItem(REFRESH_KEY);
    set({ user: null, accessToken: null });
  },
  refresh: async () => {
    const t = localStorage.getItem(REFRESH_KEY);
    if (!t) return null;
    const r = await fetch("/api/auth/refresh", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: t }) });
    if (!r.ok) { localStorage.removeItem(REFRESH_KEY); set({ user: null, accessToken: null }); return null; }
    const body = await r.json();
    localStorage.setItem(REFRESH_KEY, body.refresh_token);
    set({ accessToken: body.access_token });
    return body.access_token;
  },
  restore: async () => {
    // /auth/refresh 只回 token,不回 user;少了這步,ProtectedRoute 會因為
    // user === null 把有效 session 踢回 /login
    const token = await get().refresh();
    if (!token) { set({ bootstrapping: false }); return; }
    const r = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
    set({ user: r.ok ? await r.json() : null, bootstrapping: false });
  },
}));
