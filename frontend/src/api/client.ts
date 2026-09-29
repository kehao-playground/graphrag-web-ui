import type {
  ArtifactDetail, ArtifactPage, ArtifactTableName, GraphData,
} from "./types";
import { useAuth, refreshOnce, redirectToProxyLogin } from "../stores/auth";
import { i18n } from "../i18n";
import zhTW from "../i18n/locales/zh-TW";
import type { ErrorCode } from "../i18n";
import type { ParseKeys } from "i18next";
import { jobTypeLabel } from "../components/labels";

export async function api(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const proxy = useAuth.getState().authMode === "proxy";
  // Proxy mode sends no app token; local mode reads it (or refreshes).
  // refreshOnce rejects when the refresh endpoint is unavailable (5xx,
  // network); that propagates like a failed fetch and keeps the session.
  const token = proxy ? null : (useAuth.getState().accessToken ?? (await refreshOnce()));
  const headers = {
    // JSON only for string bodies (FastAPI 422s on the text/plain default);
    // FormData must keep the browser-set multipart boundary, so never force
    // a Content-Type there. Caller headers win.
    ...(typeof init.body === "string" ? { "Content-Type": "application/json" } : {}),
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...init.headers,
  };
  if (proxy) {
    // redirect:"manual" keeps an edge login redirect a detectable
    // opaqueredirect signal instead of a CORS TypeError (spec §6.2); a real
    // network rejection also counts as session-expired and is re-thrown
    // after scheduling the redirect.
    try {
      const r = await fetch(path, { ...init, redirect: "manual", headers });
      if (r.status === 401 || r.type === "opaqueredirect") redirectToProxyLogin();
      return r;
    } catch (err) {
      redirectToProxyLogin();
      throw err;
    }
  }
  const r = await fetch(path, { ...init, headers });
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

// Fallback keys are typed as ParseKeys so every call site is checked
// against the catalog (NOT Parameters<typeof i18n.t>[0], whose union
// includes TemplateStringsArray and breaks overload matching — verified
// against i18next 26.4.0 / TS 6.0.3).
export type FallbackKey = ParseKeys;

const isErrorCode = (c: string): c is ErrorCode => Object.hasOwn(zhTW.errors, c);

// Shared code→catalog mapping (spec §5.4): known code → localized
// message; else verbatim detail; else the fallback key.
export function messageOfBody(
  body: Record<string, unknown>,
  fallbackKey: FallbackKey,
  vars: Record<string, string | number> = {},
): string {
  const code = body.code;
  if (typeof code === "string" && isErrorCode(code)) {
    const params = typeof body.params === "object" && body.params !== null
      ? { ...(body.params as Record<string, string | number>) } : null;
    // A job type travels as its wire id ("index"); the sentence names it
    // in the reader's language (project_indexing, R3-33).
    if (params && typeof params.job_type === "string") {
      params.job_type = jobTypeLabel(params.job_type, i18n.t);
    }
    // `replace` keeps server-provided params out of the options object
    // itself, so a param named e.g. "count" or "ns" can never collide
    // with i18next's own option names.
    return i18n.t(`errors.${code}`, { ...vars, ...(params ? { replace: params } : {}) });
  }
  if (typeof body.detail === "string") return body.detail; // verbatim
  return i18n.t(fallbackKey, vars);
}

// A non-2xx answer: message is already localized (messageOfBody); status
// and the parsed body stay available for callers that branch on them
// (e.g. a 409 carrying current_hash).
export class ApiRequestError extends Error {
  readonly status: number;
  readonly body: Record<string, unknown>;
  constructor(message: string, status: number, body: Record<string, unknown>) {
    super(message);
    this.name = "ApiRequestError";
    this.status = status;
    this.body = body;
  }
}

async function requireOk(r: Response, fallbackKey: FallbackKey): Promise<Response> {
  if (r.ok) return r;
  const body = await bodyOf(r);
  throw new ApiRequestError(messageOfBody(body, fallbackKey, { status: r.status }), r.status, body);
}

// api() + the error envelope: resolve to the JSON body, or throw
// ApiRequestError. Network failures from api() propagate unchanged.
export async function apiJson<T>(path: string, fallbackKey: FallbackKey, init?: RequestInit): Promise<T> {
  const r = await requireOk(await api(path, init), fallbackKey);
  return (await r.json()) as T;
}

// Same as apiJson for calls whose success body the caller does not read.
export async function sendOk(path: string, fallbackKey: FallbackKey, init?: RequestInit): Promise<void> {
  await requireOk(await api(path, init), fallbackKey);
}

// EventSource cannot send an Authorization header, so SSE routes take the
// access token as ?token= (backend accepts it on those routes only). The
// token is read once, here, at stream-open time: subscribing to the store
// would re-open the stream on every rotation and replay it. Proxy mode
// sends no token (cookie auth); an empty token= would read as an invalid
// bearer upstream (spec §6.4).
export function sseUrl(path: string, params: Record<string, string> = {}): string {
  const usp = new URLSearchParams(params);
  const { authMode, accessToken } = useAuth.getState();
  if (authMode !== "proxy" && accessToken) usp.set("token", accessToken);
  const qs = usp.toString();
  return qs ? `${path}?${qs}` : path;
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
  return apiJson<ArtifactPage>(
    `/api/projects/${pid}/artifacts/${table}?${usp.toString()}`, "client.loadTableFailed");
}

export async function fetchArtifactDetail(
  pid: string, table: ArtifactTableName, hrid: number,
): Promise<ArtifactDetail> {
  return apiJson<ArtifactDetail>(
    `/api/projects/${pid}/artifacts/${table}/${hrid}`, "client.loadDetailFailed");
}

export async function fetchGraph(pid: string, level?: number): Promise<GraphData> {
  const qs = level !== undefined ? `?level=${level}` : "";
  return apiJson<GraphData>(`/api/projects/${pid}/artifacts/graph${qs}`, "client.loadGraphFailed");
}
