import { vi, test, expect, beforeEach } from "vitest";
import { useAuth } from "../auth";

const meBody = JSON.stringify({
  id: "u1", email: "a@b.c", display_name: "A",
  roles: [], permissions: [], is_active: true, must_change_password: false,
});

beforeEach(() => {
  localStorage.clear();
  useAuth.setState({ user: null, accessToken: null, bootstrapping: true });
});

test("parallel restore() calls share one refresh network call (single-flight)", async () => {
  localStorage.setItem("grui_refresh", "t0");
  const refreshBodies: string[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (url.includes("/api/auth/refresh")) {
      refreshBodies.push(String(init?.body));
      return new Response(JSON.stringify({ access_token: "a1", refresh_token: "t1" }), { status: 200 });
    }
    return new Response(meBody, { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);

  // StrictMode double-mount: two restore() calls in parallel
  await Promise.all([useAuth.getState().restore(), useAuth.getState().restore()]);

  expect(refreshBodies).toEqual(['{"refresh_token":"t0"}']); // exactly one refresh, same old token
  expect(localStorage.getItem("grui_refresh")).toBe("t1");   // the post-rotation new token
  expect(useAuth.getState().user?.email).toBe("a@b.c");
  expect(useAuth.getState().bootstrapping).toBe(false);
});

test("network failure during restore still clears bootstrapping (no eternal Spin)", async () => {
  localStorage.setItem("grui_refresh", "t0");
  vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("backend unreachable"); }));

  await useAuth.getState().restore();

  expect(useAuth.getState().bootstrapping).toBe(false);
  expect(useAuth.getState().user).toBeNull();
});

// ---- proxy mode (spec §6.1) ----
// resetModules + dynamic import per test: proxyRedirected is module-level,
// so each test needs a fresh stores/auth (a static import would share it).

function stubLocation() {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/projects", search: "?x=1" },
    writable: true,
  });
  return assign;
}

test("proxy restore(): config -> me -> user set, stale refresh token cleared", async () => {
  localStorage.setItem("grui_refresh", "stale");
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/auth/config")) {
      return new Response(JSON.stringify({ auth_mode: "proxy" }), { status: 200 });
    }
    if (url.includes("/api/auth/me")) {
      return new Response(JSON.stringify({
        id: "u1", email: "a@b.c", display_name: "A",
        roles: [], permissions: [], is_active: true, must_change_password: false,
      }), { status: 200 });
    }
    throw new Error("unexpected " + url);
  }));
  vi.resetModules();
  const { useAuth } = await import("../auth");

  await useAuth.getState().restore();

  expect(useAuth.getState().authMode).toBe("proxy");
  expect(useAuth.getState().user?.email).toBe("a@b.c");
  expect(useAuth.getState().bootstrapping).toBe(false);
  expect(localStorage.getItem("grui_refresh")).toBeNull();
});

test("proxy restore(): 401 from /me redirects to /oauth2/start with rd, once", async () => {
  const assign = stubLocation();
  let meCalls = 0;
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    if (url.includes("/api/auth/config")) {
      return new Response(JSON.stringify({ auth_mode: "proxy" }), { status: 200 });
    }
    meCalls += 1;
    return { ok: false, status: 401, type: "basic", json: async () => ({}) } as unknown as Response;
  }));
  vi.resetModules();
  const { useAuth, redirectToProxyLogin } = await import("../auth");

  await useAuth.getState().restore();
  redirectToProxyLogin(); // second call: suppressed by the once-guard

  expect(assign).toHaveBeenCalledTimes(1);
  expect(assign).toHaveBeenCalledWith("/oauth2/start?rd=%2Fprojects%3Fx%3D1");
  expect(meCalls).toBe(1);
  expect(useAuth.getState().user).toBeNull();
  expect(useAuth.getState().bootstrapping).toBe(false);
});

test("proxy logout(): navigates to /oauth2/sign_out with no rd and no server call", async () => {
  const assign = stubLocation();
  const fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
  vi.resetModules();
  const { useAuth } = await import("../auth");
  useAuth.setState({ authMode: "proxy", user: { email: "a@b.c" } as never, accessToken: null });

  await useAuth.getState().logout();

  expect(assign).toHaveBeenCalledWith("/oauth2/sign_out");
  expect(fetchMock).not.toHaveBeenCalled();
  expect(localStorage.getItem("grui_refresh")).toBeNull();
});

// ---- refresh failure handling and cross-tab coordination (fix wave F6) ----

function refreshMock(handler: (token: string, call: number) => Response | Promise<Response>) {
  const tokens: string[] = [];
  const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/api/auth/refresh")) {
      const token = JSON.parse(String(init?.body)).refresh_token as string;
      tokens.push(token);
      return handler(token, tokens.length);
    }
    return new Response(meBody, { status: 200 });
  });
  vi.stubGlobal("fetch", fetchMock);
  return tokens;
}

const ok = (refresh: string) =>
  new Response(JSON.stringify({ access_token: "a-" + refresh, refresh_token: refresh }), { status: 200 });

test("refresh(): a 502 keeps the token and the user and rejects (R2-10)", async () => {
  localStorage.setItem("grui_refresh", "t0");
  useAuth.setState({ user: { email: "a@b.c" } as never, accessToken: "old" });
  refreshMock(() => new Response("bad gateway", { status: 502 }));

  await expect(useAuth.getState().refresh()).rejects.toThrow();

  expect(localStorage.getItem("grui_refresh")).toBe("t0");
  expect(useAuth.getState().user?.email).toBe("a@b.c");
});

test("refresh(): a 401 ends the session", async () => {
  localStorage.setItem("grui_refresh", "t0");
  useAuth.setState({ user: { email: "a@b.c" } as never, accessToken: "old" });
  refreshMock(() => new Response("{}", { status: 401 }));

  expect(await useAuth.getState().refresh()).toBeNull();

  expect(localStorage.getItem("grui_refresh")).toBeNull();
  expect(useAuth.getState().user).toBeNull();
});

test("refresh(): a 401 after another tab rotated retries with the stored token (R2-05)", async () => {
  localStorage.setItem("grui_refresh", "t0");
  const tokens = refreshMock((token) => {
    if (token === "t0") {
      localStorage.setItem("grui_refresh", "t1"); // the other tab won
      return new Response("{}", { status: 401 });
    }
    return ok("t2");
  });

  expect(await useAuth.getState().refresh()).toBe("a-t2");

  expect(tokens).toEqual(["t0", "t1"]);
  expect(localStorage.getItem("grui_refresh")).toBe("t2");
});

test("refresh(): reads the token inside a cross-tab lock (R2-05)", async () => {
  localStorage.setItem("grui_refresh", "t0");
  const names: string[] = [];
  vi.stubGlobal("navigator", {
    ...navigator,
    locks: {
      request: async (name: string, fn: () => Promise<unknown>) => {
        names.push(name);
        // While this tab waited, the lock holder rotated and stored t1
        localStorage.setItem("grui_refresh", "t1");
        return fn();
      },
    },
  });
  const tokens = refreshMock(() => ok("t2"));
  try {
    expect(await useAuth.getState().refresh()).toBe("a-t2");
  } finally {
    vi.unstubAllGlobals();
  }

  expect(names).toEqual(["grui-refresh"]);
  expect(tokens).toEqual(["t1"]);
});

test("restore(): retries a transient refresh failure instead of logging out (R2-10)", async () => {
  vi.useFakeTimers();
  try {
    localStorage.setItem("grui_refresh", "t0");
    refreshMock((_, call) => (call === 1 ? new Response("", { status: 503 }) : ok("t1")));

    const done = useAuth.getState().restore();
    await vi.runAllTimersAsync();
    await done;
  } finally {
    vi.useRealTimers();
  }

  expect(useAuth.getState().user?.email).toBe("a@b.c");
  expect(localStorage.getItem("grui_refresh")).toBe("t1");
  expect(useAuth.getState().bootstrapping).toBe(false);
});

test("proxy logout(): later sign-in redirects cannot supersede sign-out (R1-83)", async () => {
  const assign = stubLocation();
  vi.stubGlobal("fetch", vi.fn());
  vi.resetModules();
  const { useAuth, redirectToProxyLogin } = await import("../auth");
  useAuth.setState({ authMode: "proxy", user: { email: "a@b.c" } as never, accessToken: null });

  await useAuth.getState().logout();
  redirectToProxyLogin("/"); // what Login's proxy effect does after navigate("/login")

  expect(assign).toHaveBeenCalledTimes(1);
  expect(assign).toHaveBeenCalledWith("/oauth2/sign_out");
});
