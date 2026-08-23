import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectDetail from "../ProjectDetail";
import { useAuth } from "../../stores/auth";
import type * as ApiClient from "../../api/client";

const project = {
  id: "p1", name: "P1", slug: "p1", description: null,
  input_file_type: "text", owner_id: "u1", created_at: "2026-01-01T00:00:00Z",
};
const members = [
  { user_id: "u1", email: "alice@test.local", display_name: "Alice", role: "owner" },
  { user_id: "u2", email: "bob@test.local", display_name: "Bob", role: "viewer" },
];
const users = [
  { id: "u1", email: "alice@test.local", display_name: "Alice", is_active: true },
  { id: "u2", email: "bob@test.local", display_name: "Bob", is_active: true },
  { id: "u3", email: "carol@test.local", display_name: "Carol", is_active: true },
];

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

// Real detailOf stays under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (url: string) => {
    if (url.endsWith("/members")) return json(members);
    if (url === "/api/users") return json(users);
    return json(project);
  }),
}))

beforeEach(() => {
  // alice is the project owner → canManage, so the add-member flow is rendered
  useAuth.setState({
    user: { id: "u1", email: "alice@test.local", display_name: "Alice",
            role: "user", is_active: true, must_change_password: false },
  });
})

test("owner row keeps rendering owner; add flow offers only editor/viewer", async () => {
  const qc = new QueryClient()
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/projects/p1"]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )

  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  expect(screen.getByText("bob@test.local")).toBeInTheDocument()
  // owner stays visible in the members table (single-owner policy: locked row)
  expect(screen.getByText("owner")).toBeInTheDocument()

  // add-member role select: dropdown must not offer "owner"
  const addBar = screen.getByTitle("新增成員")
  const roleSelect = within(addBar).getByText("viewer").closest(".ant-select")!
  const combobox = roleSelect.querySelector('input[role="combobox"]')!
  fireEvent.mouseDown(combobox)
  await waitFor(() => {
    const opts = Array.from(document.querySelectorAll(".ant-select-item-option-content"))
      .map((o) => o.textContent)
    expect(opts).toEqual(["editor", "viewer"])
  })
})
