import type {
  ArtifactDetail, ArtifactPage, ArtifactTableName, GraphData,
} from "./types";
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

// Backend errors are always {"detail": zh-TW}; every panel surfaces the
// detail verbatim and falls back to its own fixed message on non-JSON.
export async function bodyOf(r: Response): Promise<Record<string, unknown>> {
  try { return (await r.json()) as Record<string, unknown>; } catch { return {}; }
}

export async function detailOf(r: Response, fallback: string): Promise<string> {
  const body = await bodyOf(r);
  return typeof body.detail === "string" ? body.detail : fallback;
}

// Explore endpoints share the {"detail": zh-TW} error contract; surface the
// detail verbatim so panels can message.error it (404/409 shapes included).
async function requireOk(r: Response, fallback: string): Promise<void> {
  if (r.ok) return;
  let detail: string | undefined;
  try {
    const body: unknown = await r.json();
    if (body && typeof body === "object" && "detail" in body && typeof body.detail === "string") {
      detail = body.detail;
    }
  } catch {
    // non-JSON body → keep the fallback
  }
  throw new Error(detail ?? fallback);
}

export interface ArtifactListParams {
  limit: number;
  offset: number;
  q?: string;
  type?: string;
  community?: number;
}

export async function fetchArtifacts(
  pid: string, table: ArtifactTableName, params: ArtifactListParams,
): Promise<ArtifactPage> {
  const usp = new URLSearchParams({
    limit: String(params.limit),
    offset: String(params.offset),
  });
  if (params.q) usp.set("q", params.q);
  if (params.type) usp.set("type", params.type);
  if (params.community !== undefined) usp.set("community", String(params.community));
  const r = await api(`/api/projects/${pid}/artifacts/${table}?${usp.toString()}`);
  await requireOk(r, `載入資料表失敗(${r.status})`);
  return (await r.json()) as ArtifactPage;
}

export async function fetchArtifactDetail(
  pid: string, table: ArtifactTableName, hrid: number,
): Promise<ArtifactDetail> {
  const r = await api(`/api/projects/${pid}/artifacts/${table}/${hrid}`);
  await requireOk(r, `載入資料細節失敗(${r.status})`);
  return (await r.json()) as ArtifactDetail;
}

export async function fetchGraph(pid: string, level?: number): Promise<GraphData> {
  const qs = level !== undefined ? `?level=${level}` : "";
  const r = await api(`/api/projects/${pid}/artifacts/graph${qs}`);
  await requireOk(r, `載入圖譜失敗(${r.status})`);
  return (await r.json()) as GraphData;
}
