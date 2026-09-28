import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { ApiRequestError, apiJson, sendOk, sseUrl } from "../client";
import { useAuth } from "../../stores/auth";
import { i18n } from "../../i18n";

// apiJson/sendOk wrap api() + the error envelope (R1-81): a non-2xx answer
// throws ApiRequestError carrying the localized message, status and body.

const calls: { path: string; init: RequestInit }[] = [];
function stubFetch(status: number, body: unknown) {
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    calls.push({ path, init: init ?? {} });
    const text = body === null ? null : typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, { status });
  }));
}

beforeEach(() => {
  calls.length = 0;
  useAuth.setState({ authMode: "local", accessToken: "tok" });
});
afterEach(() => vi.unstubAllGlobals());

test("apiJson returns the parsed body on 2xx and passes init through", async () => {
  stubFetch(200, { sets: [] });
  const out = await apiJson<{ sets: unknown[] }>("/api/x", "workbench.loadSetsFailed",
    { method: "POST", body: "{}" });
  expect(out).toEqual({ sets: [] });
  expect(calls[0].path).toBe("/api/x");
  expect(calls[0].init.method).toBe("POST");
  expect(calls[0].init.headers).toMatchObject({
    Authorization: "Bearer tok", "Content-Type": "application/json",
  });
});

test("apiJson throws ApiRequestError with status, body and the verbatim detail", async () => {
  stubFetch(409, { detail: "hash mismatch", current_hash: "h2" });
  const err = await apiJson("/api/x", "settings.saveFailed").catch((e: unknown) => e);
  expect(err).toBeInstanceOf(ApiRequestError);
  expect(err).toBeInstanceOf(Error);
  const e = err as ApiRequestError;
  expect(e.status).toBe(409);
  expect(e.body).toEqual({ detail: "hash mismatch", current_hash: "h2" });
  expect(e.message).toBe("hash mismatch");
});

test("apiJson localizes a known error code", async () => {
  stubFetch(409, { detail: "x", code: "project_indexing" });
  const e = (await apiJson("/api/x", "settings.saveFailed").catch((x: unknown) => x)) as ApiRequestError;
  expect(e.message).toBe(i18n.t("errors.project_indexing"));
});

test("apiJson falls back to the key on a non-JSON error body", async () => {
  stubFetch(502, "<html>");
  const e = (await apiJson("/api/x", "jobs.loadPreflightFailed").catch((x: unknown) => x)) as ApiRequestError;
  expect(e.status).toBe(502);
  expect(e.body).toEqual({});
  expect(e.message).toBe(i18n.t("jobs.loadPreflightFailed", { status: 502 }));
});

test("apiJson falls back to the key when detail is absent or not a string", async () => {
  for (const body of [{ other: 1 }, { detail: { nested: true } }]) {
    stubFetch(400, body);
    const e = (await apiJson("/api/x", "files.loadFailed").catch((x: unknown) => x)) as ApiRequestError;
    expect(e.message).toBe(i18n.t("files.loadFailed", { status: 400 }));
  }
});

test("sendOk resolves without reading a body and throws on failure", async () => {
  stubFetch(204, null);
  await expect(sendOk("/api/x", "files.deleteFailed", { method: "DELETE" })).resolves.toBeUndefined();
  stubFetch(403, { detail: "nope" });
  await expect(sendOk("/api/x", "files.deleteFailed")).rejects.toMatchObject({ status: 403, message: "nope" });
});

test("sseUrl adds the access token read at call time and encodes params", () => {
  expect(sseUrl("/api/jobs/1/logs")).toBe("/api/jobs/1/logs?token=tok");
  expect(sseUrl("/api/q", { query: "a b&c", method: "local" }))
    .toBe("/api/q?query=a+b%26c&method=local&token=tok");
});

test("sseUrl omits the token when there is none and in proxy mode", () => {
  useAuth.setState({ authMode: "local", accessToken: null });
  expect(sseUrl("/api/jobs/1/logs")).toBe("/api/jobs/1/logs");
  useAuth.setState({ authMode: "proxy", accessToken: "stale" });
  expect(sseUrl("/api/q", { method: "local" })).toBe("/api/q?method=local");
});
