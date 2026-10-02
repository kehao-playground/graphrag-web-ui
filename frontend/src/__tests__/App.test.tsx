import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAuth } from "../stores/auth";

// Route smoke for the real router (R2-35): App builds its browser router at
// import time from window.location, so each case sets the URL, then imports
// a fresh module graph.

const me = {
  id: "u1", email: "alice@test.local", display_name: "Alice", roles: [], permissions: [],
  is_active: true, must_change_password: false,
};

function serve(signedIn: boolean) {
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status });
    if (url === "/api/auth/config") return json(200, { auth_mode: "local" });
    if (url === "/api/auth/refresh") {
      return signedIn
        ? json(200, { access_token: "a1", refresh_token: "r1" })
        : json(401, { detail: "invalid", code: "auth_invalid_token" });
    }
    if (url === "/api/auth/me") return json(200, me);
    if (url.startsWith("/api/projects")) return json(200, []);
    return json(200, {});
  }));
}

async function boot(path: string) {
  window.history.replaceState(null, "", path);
  vi.resetModules();
  // the fresh module graph has its own store and i18n instance; seed them
  // like a page load in the language the assertions use
  const { i18n } = await import("../i18n");
  await i18n.changeLanguage("zh-TW");
  const { useAuth: freshAuth } = await import("../stores/auth");
  freshAuth.setState({ user: null, accessToken: null, bootstrapping: true, authMode: null });
  const { default: App } = await import("../App");
  render(<App />);
}

beforeEach(() => localStorage.clear());
afterEach(() => {
  cleanup();
  useAuth.setState({ user: null, accessToken: null });
});

it("a signed-out deep link lands on the sign-in form", async () => {
  serve(false);
  await boot("/projects");
  expect(await screen.findByRole("button", { name: /登\s*入/ })).toBeInTheDocument();
  expect(window.location.pathname).toBe("/login");
});

it("a stored session restores and / opens the project list", async () => {
  localStorage.setItem("grui_refresh", "r0");
  serve(true);
  await boot("/");
  await waitFor(() => expect(window.location.pathname).toBe("/projects"));
  expect(await screen.findByText("alice@test.local", { exact: false })).toBeInTheDocument();
});

it("an admin route without users:manage renders the 403 page", async () => {
  localStorage.setItem("grui_refresh", "r0");
  serve(true);
  await boot("/admin/users");
  expect(await screen.findByText("沒有權限檢視此頁面")).toBeInTheDocument();
});
