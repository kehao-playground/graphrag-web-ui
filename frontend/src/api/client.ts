import type {
  ArtifactDetail, ArtifactPage, ArtifactTableName, GraphData,
} from "./types";
import { useAuth, refreshOnce, redirectToProxyLogin } from "../stores/auth";
import { i18n } from "../i18n";
import zhTW from "../i18n/locales/zh-TW";
import type { ErrorCode } from "../i18n";
import type { ParseKeys } from "i18next";

export async function api(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  if (useAuth.getState().authMode === "proxy") {
    // Proxy mode: no app token. redirect:"manual" keeps an edge login
    // redirect a detectable opaqueredirect signal instead of a CORS
    // TypeError (spec §6.2); a real network rejection also counts as
    // session-expired and is re-thrown after scheduling the redirect.
    try {
      const r = await fetch(path, {
        ...init,
        redirect: "manual",
        headers: {
          // Same JSON-only rule as the local branch below: string bodies
          // are JSON (FastAPI 422s on the text/plain default), FormData
          // keeps the browser-set multipart boundary, caller headers win.
          ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
          ...init.headers,
        },
      });
      if (r.status === 401 || r.type === "opaqueredirect") redirectToProxyLogin();
      return r;
    } catch (err) {
      redirectToProxyLogin();
      throw err;
    }
  }
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

// Backend error envelope: {"detail"?, "code"?, "params"?} (i18n spec §4.1).
// detail is an English developer-facing string; code/params are what the UI
// localizes from. detail is only ever shown verbatim as a last resort, for a
// code this build's catalog does not know.
export async function bodyOf(r: Response): Promise<Record<string, unknown>> {
  try { return (await r.json()) as Record<string, unknown>; } catch { return {}; }
}

// Dynamic string keys can't satisfy the typed key union; the cast is
// confined to the fallback leg (the error-code leg narrows for real).
// ParseKeys (NOT Parameters<typeof i18n.t>[0], whose union includes
// TemplateStringsArray and breaks overload matching — verified against
// i18next 26.4.0 / TS 6.0.3).
type AnyTKey = ParseKeys;

const isErrorCode = (c: string): c is ErrorCode => Object.hasOwn(zhTW.errors, c);

// Shared code→catalog mapping (spec §5.4): known code → localized
// message; else verbatim detail; else the fallback key.
export function messageOfBody(
  body: Record<string, unknown>,
  fallbackKey: string,
  vars: Record<string, string | number> = {},
): string {
  const code = body.code;
  if (typeof code === "string" && isErrorCode(code)) {
    const params = body.params;
    // `replace` keeps server-provided params out of the options object
    // itself, so a param named e.g. "count" or "ns" can never collide
    // with i18next's own option names.
    return i18n.t(`errors.${code}`, {
      ...vars,
      ...(typeof params === "object" && params !== null
           ? { replace: params as Record<string, string | number> } : {}),
    });
  }
  if (typeof body.detail === "string") return body.detail; // verbatim
  return i18n.t(fallbackKey as AnyTKey, vars);
}

export async function detailOf(r: Response, fallbackKey: string): Promise<string> {
  const body = await bodyOf(r);
  return messageOfBody(body, fallbackKey, { status: r.status });
}

// Explore endpoints share the error envelope; messageOfBody localizes the
// code or surfaces the detail verbatim (404/409 shapes included).
async function requireOk(r: Response, fallback: string): Promise<void> {
  if (r.ok) return;
  let parsedBody: Record<string, unknown> = {};
  try {
    parsedBody = ((await r.json()) ?? {}) as Record<string, unknown>;
  } catch {
    // non-JSON body → messageOfBody falls back to the key
  }
  throw new Error(messageOfBody(parsedBody, fallback, { status: r.status }));
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
  await requireOk(r, "client.loadTableFailed");
  return (await r.json()) as ArtifactPage;
}

export async function fetchArtifactDetail(
  pid: string, table: ArtifactTableName, hrid: number,
): Promise<ArtifactDetail> {
  const r = await api(`/api/projects/${pid}/artifacts/${table}/${hrid}`);
  await requireOk(r, "client.loadDetailFailed");
  return (await r.json()) as ArtifactDetail;
}

export async function fetchGraph(pid: string, level?: number): Promise<GraphData> {
  const qs = level !== undefined ? `?level=${level}` : "";
  const r = await api(`/api/projects/${pid}/artifacts/graph${qs}`);
  await requireOk(r, "client.loadGraphFailed");
  return (await r.json()) as GraphData;
}
