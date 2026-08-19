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

export const useAuth = create<AuthState>((set) => ({
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
    // 走 single-flight(refreshOnce):dev StrictMode double-mount 會並行呼叫
    // 兩次 restore,若各自直接 refresh,第二個請求拿著已被輪替作廢的 token
    // → 401 → 有效 session 被清掉。
    try {
      const token = await refreshOnce();
      if (!token) { set({ bootstrapping: false }); return; }
      const r = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
      set({ user: r.ok ? await r.json() : null, bootstrapping: false });
    } catch {
      // 後端不可達等 network 錯誤:仍必須收斂,否則 bootstrapping 永遠 true → 永遠 Spin
      set({ user: null, bootstrapping: false });
    }
  },
}));

// refresh 是輪替式的:多個請求同時 401 各自去 refresh,第一個成功後
// 其餘拿著已作廢的 token → 全部 401 → 使用者被登出。頁面載入時通常就有
// 3-4 個並行請求,所以這個 single-flight 是必要的,不是最佳化。
// 放在這裡(而非 client.ts)讓 restore() 也能共用同一班機,避免循環 import。
let inflight: Promise<string | null> | null = null;

export function refreshOnce(): Promise<string | null> {
  if (!inflight) {
    inflight = useAuth.getState().refresh().finally(() => { inflight = null });
  }
  return inflight;
}
