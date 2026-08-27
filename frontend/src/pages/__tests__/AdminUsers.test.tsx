import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AdminUsers from "../AdminUsers";
import type * as ApiClient from "../../api/client";
import { useAuth } from "../../stores/auth";

// No RTL auto-cleanup here: prior renders leak rows into later tests.
afterEach(cleanup)

// Real detailOf stays under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  // A Response body is single-use; build a fresh one per api() call so
  // later tests in this file still get a readable body.
  api: vi.fn(async () => new Response(JSON.stringify([
    { id: "u1", email: "alice@test.local", display_name: "Alice", role: "admin", is_active: true, must_change_password: false },
    { id: "u2", email: "bob@test.local", display_name: "Bob", role: "user", is_active: false, must_change_password: true },
  ]), { status: 200 })),
}))

test("renders user list", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><AdminUsers /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  expect(await screen.findByText("bob@test.local")).toBeInTheDocument()
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
    user: { id: "me", email: "me@b.c", role: "admin" } as never,
  })
  mountAdminUsers()
  await waitFor(() => expect(screen.getByText("alice@test.local")).toBeInTheDocument())
  expect(screen.queryByTestId("reset-password-button")).toBeNull()
})

test("local mode: reset-password action shown", async () => {
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", role: "admin" } as never,
  })
  mountAdminUsers()
  await waitFor(() =>
    expect(screen.getAllByTestId("reset-password-button").length).toBeGreaterThan(0))
})
