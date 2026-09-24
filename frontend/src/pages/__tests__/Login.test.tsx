import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import Login from "../Login";
import { useAuth } from "../../stores/auth";

// No RTL auto-cleanup here: prior renders leak buttons into later tests.
afterEach(cleanup)

test("submits credentials and shows error on 401", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ detail: "unauthorized" }), { status: 401 }))
  vi.stubGlobal("fetch", fetchMock)
  // Login uses useNavigate() → without a Router it throws outright
  render(<MemoryRouter><Login /></MemoryRouter>)
  // Labels must match the implemented zh-TW copy, not /email/i
  await userEvent.type(screen.getByLabelText("電子郵件"), "a@b.c")
  await userEvent.type(screen.getByLabelText("密碼"), "wrong")
  await userEvent.click(screen.getByRole("button", { name: /登入/ }))
  await waitFor(() => expect(screen.getByText(/登入失敗/)).toBeInTheDocument())
})

test("proxy mode: renders nothing and redirects to /oauth2/start", () => {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    value: { ...window.location, assign, pathname: "/login", search: "" },
    writable: true,
  });
  useAuth.setState({ authMode: "proxy" });

  render(<MemoryRouter><Login /></MemoryRouter>)

  expect(screen.queryByRole("button")).toBeNull()
  // rd must NOT be /login: /oauth2/start with a live session 302s straight
  // back to rd, and a /login rd loops forever (found in live smoke test)
  expect(assign).toHaveBeenCalledWith("/oauth2/start?rd=%2F")
})

test("local mode: renders the password form", () => {
  // Input.Password's eye toggle is also role="button"; pin the submit by label.
  useAuth.setState({ authMode: "local" })
  render(<MemoryRouter><Login /></MemoryRouter>)
  expect(screen.getByRole("button", { name: /登入/ })).toBeInTheDocument()
})

async function reachChangePassword(changePassword: () => Promise<Response>) {
  useAuth.setState({ authMode: "local", user: null, accessToken: null })
  const calls: string[] = []
  vi.stubGlobal("fetch", vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
    const url = String(input)
    calls.push(url)
    if (url.includes("/api/auth/login")) {
      return new Response(JSON.stringify({
        access_token: "a0", refresh_token: "t0",
        user: { email: "a@b.c", must_change_password: true },
      }), { status: 200 })
    }
    if (url.includes("/api/auth/refresh")) {
      return new Response(JSON.stringify({ access_token: "a1", refresh_token: "t1" }), { status: 200 })
    }
    if (url.includes("/api/auth/change-password")) {
      calls.push(new Headers(init?.headers).get("Authorization") ?? "")
      return changePassword()
    }
    throw new Error("unexpected " + url)
  }))
  render(<MemoryRouter><Login /></MemoryRouter>)
  await userEvent.type(screen.getByLabelText("電子郵件"), "a@b.c")
  await userEvent.type(screen.getByLabelText("密碼"), "admin-pass-123")
  await userEvent.click(screen.getByRole("button", { name: /登入/ }))
  await userEvent.type(await screen.findByLabelText("目前密碼"), "admin-pass-123")
  await userEvent.type(screen.getByLabelText("新密碼"), "new-pass-456")
  await userEvent.click(screen.getByRole("button", { name: /送\s*出/ }))
  return calls
}

test("change-password: a network failure clears the loading state and says so (R1-116, R2-31)", async () => {
  await reachChangePassword(async () => { throw new TypeError("network down") })
  await waitFor(() => expect(screen.getByText(/網路/)).toBeInTheDocument())
  expect(screen.getByRole("button", { name: /送\s*出/ })).not.toHaveClass("ant-btn-loading")
})

test("change-password goes through api(): a 401 refreshes and retries (R1-55)", async () => {
  let n = 0
  const calls = await reachChangePassword(async () =>
    ++n === 1 ? new Response("{}", { status: 401 }) : new Response(null, { status: 204 }))
  await waitFor(() => expect(n).toBe(2))
  expect(calls).toContain("/api/auth/refresh")
  expect(calls).toContain("Bearer a1")
})
