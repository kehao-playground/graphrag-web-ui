import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AdminUsers from "../AdminUsers";

vi.mock("../../api/client", () => ({
  api: vi.fn().mockResolvedValue(new Response(JSON.stringify([
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
