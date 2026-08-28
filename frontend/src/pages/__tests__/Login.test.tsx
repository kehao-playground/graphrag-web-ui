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
