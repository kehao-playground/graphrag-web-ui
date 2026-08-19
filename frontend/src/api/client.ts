import { useAuth, refreshOnce } from "../stores/auth";

export async function api(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const token = useAuth.getState().accessToken ?? (await refreshOnce());
  const r = await fetch(path, {
    ...init,
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init.headers,
    },
  });
  if (r.status === 401 && !retried) {
    const fresh = await refreshOnce();
    if (fresh) return api(path, init, true);
  }
  return r;
}
