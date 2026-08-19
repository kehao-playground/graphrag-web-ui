import { useAuth } from "../stores/auth";

// refresh 是輪替式的:多個請求同時 401 各自去 refresh,第一個成功後
// 其餘拿著已作廢的 token → 全部 401 → 使用者被登出。頁面載入時通常就有
// 3-4 個並行請求,所以這個 single-flight 是必要的,不是最佳化。
let inflight: Promise<string | null> | null = null;

function refreshOnce(): Promise<string | null> {
  if (!inflight) {
    inflight = useAuth.getState().refresh().finally(() => { inflight = null });
  }
  return inflight;
}

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
