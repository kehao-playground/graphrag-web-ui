import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { i18n } from "../../i18n";
import { useAuth } from "../../stores/auth";
import Layout from "../Layout";

function mount() {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [],
            permissions: ["users:manage"],
            is_active: true, must_change_password: false },
  });
  return render(<MemoryRouter><Layout /></MemoryRouter>);
}

afterEach(async () => { await i18n.changeLanguage("zh-TW"); });

it("language dropdown toggles nav copy and documentElement.lang", async () => {
  mount();
  expect(screen.getByText("專案")).toBeInTheDocument();
  // the users:manage atom (not a role name) is what shows the admin entry
  expect(screen.getByText("管理者 — 使用者")).toBeInTheDocument();
  // the selector is a Select dropdown at the Sider bottom: open it, then
  // pick the option — the closed control only shows the current language
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "語言" }));
  await user.click(await screen.findByText("English"));
  expect(screen.getByText("Projects")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("en-US");
  await user.click(screen.getByRole("combobox", { name: "Language" }));
  await user.click(await screen.findByText("中文"));
  expect(screen.getByText("專案")).toBeInTheDocument();
  expect(document.documentElement.lang).toBe("zh-TW");
});

it("hides the admin nav entry without the users:manage atom", () => {
  useAuth.setState({
    accessToken: "t",
    user: { id: "u1", email: "a@b.c", display_name: "A", roles: [],
            permissions: [], is_active: true, must_change_password: false },
  });
  render(<MemoryRouter><Layout /></MemoryRouter>);
  expect(screen.getByText("專案")).toBeInTheDocument();
  expect(screen.queryByText("管理者 — 使用者")).toBeNull();
});

describe("change password (R3-09)", () => {
  afterEach(() => { vi.unstubAllGlobals(); useAuth.setState({ authMode: null }); });

  it("the user menu offers it in local mode and re-signs in with the new password", async () => {
    const calls: { url: string; body?: string }[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: string, init?: RequestInit) => {
      calls.push({ url: String(input), body: init?.body as string | undefined });
      if (input === "/api/auth/change-password") return new Response(null, { status: 204 });
      if (input === "/api/auth/login") {
        return new Response(JSON.stringify({
          access_token: "a2", refresh_token: "t2",
          user: { id: "u1", email: "a@b.c", display_name: "A", roles: [], permissions: [],
                  is_active: true, must_change_password: false },
        }), { status: 200 });
      }
      throw new Error("unexpected " + input);
    }));
    useAuth.setState({ authMode: "local" });
    mount();
    const user = userEvent.setup();
    await user.click(screen.getByText("變更密碼"));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("目前密碼"), "old-pass-123");
    await user.type(within(dialog).getByLabelText("新密碼"), "new-pass-456");
    await user.click(within(dialog).getByRole("button", { name: /送\s*出/ }));
    await waitFor(() => expect(calls.map((c) => c.url)).toEqual([
      "/api/auth/change-password", "/api/auth/login",
    ]));
    expect(JSON.parse(calls[1].body!)).toEqual({ email: "a@b.c", password: "new-pass-456" });
    expect(await screen.findByText("密碼已變更")).toBeInTheDocument();
  });

  it("a wrong current password stays in the dialog with a field error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify({ code: "auth_wrong_current_password" }), { status: 400 })));
    useAuth.setState({ authMode: "local" });
    mount();
    const user = userEvent.setup();
    await user.click(screen.getByText("變更密碼"));
    const dialog = await screen.findByRole("dialog");
    await user.type(within(dialog).getByLabelText("目前密碼"), "nope-nope");
    await user.type(within(dialog).getByLabelText("新密碼"), "new-pass-456");
    await user.click(within(dialog).getByRole("button", { name: /送\s*出/ }));
    expect(await within(dialog).findByText("原密碼錯誤")).toBeInTheDocument();
  });

  it("proxy mode has no change-password entry (the IdP owns passwords)", () => {
    useAuth.setState({ authMode: "proxy" });
    mount();
    expect(screen.queryByText("變更密碼")).toBeNull();
  });
});
