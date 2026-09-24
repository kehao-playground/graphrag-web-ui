import { create } from "zustand";
import type { User } from "../api/types";

interface AuthState {
  user: User | null;
  accessToken: string | null;
  authMode: "local" | "proxy" | null; // null until /api/auth/config resolves; treated as local so local boots are unchanged
  bootstrapping: boolean;          // true while restoring the session after a page reload
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => Promise<void>;
  refresh: () => Promise<string | null>; // null = no session; rejects = try later (RefreshUnavailableError)
  restore: () => Promise<void>;
}
const REFRESH_KEY = "grui_refresh";

export const useAuth = create<AuthState>((set) => ({
  user: null,
  accessToken: null,
  authMode: null,
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
    if (useAuth.getState().authMode === "proxy") {
      // Nothing to revoke server-side (no refresh tokens). No `rd`: landing
      // back in the app would silently re-authenticate against the live IdP
      // session (spec decision 6).
      localStorage.removeItem(REFRESH_KEY);
      // The page is leaving for sign-out: no later sign-in redirect (Login's
      // proxy effect after the caller navigates to /login, a racing 401)
      // may supersede it and re-authenticate (R1-83).
      proxyRedirected = true;
      window.location.assign("/oauth2/sign_out");
      return;
    }
    const t = localStorage.getItem(REFRESH_KEY);
    try {
      if (t) await fetch("/api/auth/logout", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: t }) });
    } finally {
      // Even on a network error the local session must be cleared, or the user can never log out
      localStorage.removeItem(REFRESH_KEY);
      set({ user: null, accessToken: null });
    }
  },
  refresh: () => withRefreshLock(() => refreshWith(set)),
  restore: async () => {
    // /auth/refresh returns only tokens, not the user; without this step
    // ProtectedRoute would bounce a valid session back to /login because
    // user === null.
    // Uses single-flight (refreshOnce): dev StrictMode double-mount calls
    // restore twice in parallel; if each refreshed directly, the second
    // request would carry an already-rotated, revoked token
    // → 401 → the valid session gets cleared.
    try {
      const cfgR = await fetch("/api/auth/config", { redirect: "manual" });
      const cfg = cfgR.ok ? await cfgR.json() : null;
      // config unreachable or malformed: assume local, existing behavior
      const mode = cfg?.auth_mode === "proxy" ? "proxy" : "local";
      set({ authMode: mode });
      if (mode === "proxy") {
        // No app tokens exist in proxy mode; a stale local-mode refresh token
        // must not linger (spec §6.1)
        localStorage.removeItem(REFRESH_KEY);
        const r = await fetch("/api/auth/me", { redirect: "manual" });
        if (r.status === 401 || r.type === "opaqueredirect") {
          set({ user: null, accessToken: null, bootstrapping: false });
          redirectToProxyLogin();
          return;
        }
        set({ user: r.ok ? await r.json() : null, bootstrapping: false });
        return;
      }
      const token = await restoreRefresh();
      if (!token) { set({ bootstrapping: false }); return; }
      const r = await fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } });
      set({ user: r.ok ? await r.json() : null, bootstrapping: false });
    } catch {
      // Backend-unreachable and other network errors must still converge, or
      // bootstrapping stays true forever → an eternal Spin
      set({ user: null, bootstrapping: false });
    }
  },
}));

// Proxy-mode sign-in redirect (spec §6.1). Once per page load: a
// valid-but-stale proxy cookie makes /oauth2/start bounce straight back
// and a second redirect would loop start→rd→401→start.
let proxyRedirected = false;
export function redirectToProxyLogin(rd: string = location.pathname + location.search): void {
  if (proxyRedirected) return;
  proxyRedirected = true;
  window.location.assign("/oauth2/start?rd=" + encodeURIComponent(rd));
}

// refresh is rotating: when several concurrent 401s each refresh on their
// own, the first success invalidates the tokens the rest are holding
// → all 401 → the user is logged out. A page load typically fires 3-4
// concurrent requests, so this single-flight is a necessity, not an optimization.
// Living here (not in client.ts) lets restore() board the same flight and avoids a circular import.
let inflight: Promise<string | null> | null = null;

export function refreshOnce(): Promise<string | null> {
  if (!inflight) {
    inflight = useAuth.getState().refresh().finally(() => { inflight = null });
  }
  return inflight;
}

// A refresh failure that says nothing about the token: a 5xx/429 during a
// deploy, say. The token and the user stay; only a 401 ends the session
// (R2-10). Network errors reject the same way, as a TypeError from fetch.
export class RefreshUnavailableError extends Error {
  readonly status: number;
  constructor(status: number) {
    super(`refresh unavailable (${status})`);
    this.status = status;
  }
}

// Tabs share one refresh token in localStorage, and single-flight only
// covers this tab. The lock serializes refreshes across tabs, and the token
// is read inside it, so a tab that waited uses the one the holder stored
// instead of re-presenting the consumed one (R2-05). Without the Locks API
// the backend's reuse grace window still absorbs the double-present.
function withRefreshLock<T>(fn: () => Promise<T>): Promise<T> {
  const locks = typeof navigator === "undefined" ? undefined : navigator.locks;
  return locks ? locks.request("grui-refresh", fn) : fn();
}

type SetAuth = (partial: Partial<AuthState>) => void;

async function refreshWith(set: SetAuth, retried = false): Promise<string | null> {
  const t = localStorage.getItem(REFRESH_KEY);
  if (!t) return null;
  const r = await fetch("/api/auth/refresh", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: t }) });
  if (r.status === 401) {
    const stored = localStorage.getItem(REFRESH_KEY);
    // Another tab rotated while this request was in flight: its token is
    // the live one, not a sign that the session is over.
    if (stored && stored !== t && !retried) return refreshWith(set, true);
    if (stored === t) localStorage.removeItem(REFRESH_KEY);
    set({ user: null, accessToken: null });
    return null;
  }
  if (!r.ok) throw new RefreshUnavailableError(r.status);
  const body = await r.json();
  localStorage.setItem(REFRESH_KEY, body.refresh_token);
  set({ accessToken: body.access_token });
  return body.access_token;
}

// Page-load refresh: a backend restarting under a deploy answers 502/503 or
// refuses connections for a few seconds; retry through that instead of
// showing the login page to a user whose session is fine.
const RESTORE_RETRY_DELAYS_MS = [1000, 2000, 4000];

async function restoreRefresh(): Promise<string | null> {
  for (let attempt = 0; ; attempt++) {
    try {
      return await refreshOnce();
    } catch (err) {
      if (attempt >= RESTORE_RETRY_DELAYS_MS.length) throw err;
      await new Promise((resolve) => setTimeout(resolve, RESTORE_RETRY_DELAYS_MS[attempt]));
    }
  }
}
