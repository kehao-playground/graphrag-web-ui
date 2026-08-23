import { vi, test, expect, beforeEach } from "vitest";
import { useAuth } from "../auth";

const meBody = JSON.stringify({
  id: "u1", email: "a@b.c", display_name: "A",
  role: "user", is_active: true, must_change_password: false,
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
