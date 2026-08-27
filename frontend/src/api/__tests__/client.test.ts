import { afterEach, beforeEach, expect, test, vi, type Mock } from "vitest";
import { bodyOf, detailOf } from "../client";

test("detailOf surfaces zh-TW detail verbatim", async () => {
  const r = new Response(JSON.stringify({ detail: "找不到該筆資料" }), { status: 404 });
  expect(await detailOf(r, "fallback")).toBe("找不到該筆資料");
});

test("detailOf falls back on non-JSON body", async () => {
  const r = new Response("<html>", { status: 502 });
  expect(await detailOf(r, "fallback")).toBe("fallback");
});

test("detailOf falls back when detail is absent or non-string", async () => {
  const missing = new Response(JSON.stringify({ other: 1 }), { status: 409 });
  expect(await detailOf(missing, "fallback")).toBe("fallback");
  const object = new Response(JSON.stringify({ detail: { nested: true } }), { status: 400 });
  expect(await detailOf(object, "fallback")).toBe("fallback");
});

test("bodyOf returns parsed body, empty object on non-JSON", async () => {
  const json = new Response(JSON.stringify({ current_hash: "h1" }), { status: 409 });
  expect(await bodyOf(json)).toEqual({ current_hash: "h1" });
  const html = new Response("<html>", { status: 502 });
  expect(await bodyOf(html)).toEqual({});
});

// ---- proxy mode (spec §6.2) ----
// resetModules + dynamic import per test: api()'s proxy branch consults the
// store and redirectToProxyLogin() has a module-level once-guard, so static
// imports would leak both across tests.

let assign: Mock;
beforeEach(() => {
  assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/", search: "" },
    writable: true,
  });
});
afterEach(() => {
  vi.unstubAllGlobals();
});

test("proxy mode: no Authorization header, no refresh, 401 redirects once", async () => {
  const calls: RequestInit[] = [];
  vi.stubGlobal("fetch", vi.fn(async (_p: string, init?: RequestInit) => {
    calls.push(init ?? {});
    return { ok: false, status: 401, type: "basic", json: async () => ({}) } as unknown as Response;
  }));
  vi.resetModules();
  const { api } = await import("../client");
  const { useAuth } = await import("../../stores/auth");
  useAuth.setState({ authMode: "proxy", accessToken: null });

  const r = await api("/api/projects");

  expect(r.status).toBe(401);
  expect(calls).toHaveLength(1);
  // no Authorization attached (the proxy branch passes no headers at all)
  expect((calls[0].headers ?? {}) as Record<string, unknown>).not.toHaveProperty("Authorization");
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: opaqueredirect response also redirects", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    ({ ok: false, status: 0, type: "opaqueredirect" }) as unknown as Response));
  vi.resetModules();
  const { api } = await import("../client");
  const { useAuth } = await import("../../stores/auth");
  useAuth.setState({ authMode: "proxy" });

  await api("/api/projects");
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: rejected fetch schedules redirect then re-throws", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("network down"); }));
  vi.resetModules();
  const { api } = await import("../client");
  const { useAuth } = await import("../../stores/auth");
  useAuth.setState({ authMode: "proxy" });

  await expect(api("/api/projects")).rejects.toThrow("network down");
  expect(assign).toHaveBeenCalledTimes(1);
});

test("proxy mode: 403 does NOT redirect (account disabled is a normal error)", async () => {
  vi.stubGlobal("fetch", vi.fn(async () =>
    ({ ok: false, status: 403, type: "basic", json: async () => ({}) }) as unknown as Response));
  vi.resetModules();
  const { api } = await import("../client");
  const { useAuth } = await import("../../stores/auth");
  useAuth.setState({ authMode: "proxy" });

  const r = await api("/api/projects");
  expect(r.status).toBe(403);
  expect(assign).not.toHaveBeenCalled();
});
