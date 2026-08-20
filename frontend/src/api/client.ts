import { useAuth, refreshOnce } from "../stores/auth";

export async function api(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const token = useAuth.getState().accessToken ?? (await refreshOnce());
  const r = await fetch(path, {
    ...init,
    headers: {
      // JSON only for string bodies; FormData must keep the browser-set
      // multipart boundary, so never force a Content-Type there.
      ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
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
