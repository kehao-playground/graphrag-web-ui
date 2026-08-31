import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AdminUsers from "../AdminUsers";
import type * as ApiClient from "../../api/client";
import { useAuth } from "../../stores/auth";

// No RTL auto-cleanup here: prior renders leak rows into later tests.
afterEach(cleanup)

// The backend seed's two global built-ins, in the catalog's (scope, name) order.
const globalRoles = [
  { id: "00000000-0000-4000-8000-000000000002", scope: "global", name: "ops",
    description: "Operate every project",
    permissions: ["projects:view_any", "projects:act_any"], is_system: true },
  { id: "00000000-0000-4000-8000-000000000001", scope: "global", name: "user_admin",
    description: "Manage users and roles",
    permissions: ["users:manage"], is_system: true },
];
const userAdminRole = globalRoles[1];

// Real detailOf stays under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  // A Response body is single-use; build a fresh one per api() call so
  // later tests in this file still get a readable body.
  api: vi.fn(async (url: string) => {
    if (url === "/api/roles?scope=global") {
      return new Response(JSON.stringify(globalRoles), { status: 200 });
    }
    return new Response(JSON.stringify([
      { id: "u1", email: "alice@test.local", display_name: "Alice",
        roles: [userAdminRole], permissions: ["users:manage"],
        is_active: true, must_change_password: false },
      { id: "u2", email: "bob@test.local", display_name: "Bob",
        roles: [], permissions: [],
        is_active: false, must_change_password: true },
    ]), { status: 200 });
  }),
}))

test("renders user list", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><AdminUsers /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  expect(await screen.findByText("bob@test.local")).toBeInTheDocument()
  // row actions render: an Edit button per user (antd spaces 2-char labels)
  expect(screen.getAllByRole("button", { name: /編\s*輯/ }).length).toBe(2)
})

test("role column renders localized built-in labels and — for zero roles", async () => {
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] } as never,
  })
  mountAdminUsers()
  await waitFor(() => expect(screen.getByText("alice@test.local")).toBeInTheDocument())
  // alice holds user_admin (zh-TW label); bob holds no role at all
  expect(screen.getByText("使用者管理員")).toBeInTheDocument()
  expect(screen.getByText("—")).toBeInTheDocument()
})

function mountAdminUsers() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><AdminUsers /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test("proxy mode: reset-password action hidden", async () => {
  useAuth.setState({
    authMode: "proxy", accessToken: null,
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] } as never,
  })
  mountAdminUsers()
  await waitFor(() => expect(screen.getByText("alice@test.local")).toBeInTheDocument())
  expect(screen.queryByTestId("reset-password-button")).toBeNull()
})

test("local mode: reset-password action shown", async () => {
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] } as never,
  })
  mountAdminUsers()
  await waitFor(() =>
    expect(screen.getAllByTestId("reset-password-button").length).toBeGreaterThan(0))
})
