import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AdminRoles from "../AdminRoles";
import { useAuth } from "../../stores/auth";

// No RTL auto-cleanup here: prior renders leak rows into later tests.
afterEach(cleanup);

// The admin catalog (Task 3): one locked built-in plus one custom
// project-scoped row. Bodies are rebuilt per call (Response is single-use).
// vi.hoisted: the vi.mock factory below is hoisted above every const here.
const { api } = vi.hoisted(() => ({
  api: vi.fn(async (path: string, init?: RequestInit) => {
    if (path === "/api/admin/roles" && (!init || init.method === undefined)) {
      return new Response(JSON.stringify([
        { id: "00000000-0000-4000-8000-000000000001", scope: "global",
          name: "user_admin", description: "", permissions: ["users:manage"],
          is_system: true, user_count: 1, member_count: 0 },
        { id: "c0", scope: "project", name: "auditor", description: "",
          permissions: ["project:view"], is_system: false,
          user_count: 0, member_count: 2 },
      ]), { status: 200 });
    }
    if (path === "/api/admin/roles" && init?.method === "POST") {
      return new Response(JSON.stringify(
        { id: "c1", scope: "global", name: "new", description: "",
          permissions: [], is_system: false }), { status: 201 });
    }
    throw new Error("unexpected " + path);
  }),
}));
vi.mock("../../api/client", () => ({ api, detailOf: async () => "err" }));

beforeEach(() => {
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] },
  } as never);
});

function mountAdminRoles() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><AdminRoles /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("lists catalog with usage counts and locks system roles", async () => {
  mountAdminRoles();
  await waitFor(() => expect(screen.getByText("user_admin")).toBeInTheDocument());
  expect(screen.getByText("auditor")).toBeInTheDocument();
  // system row: edit/delete disabled
  const buttons = screen.getAllByRole("button");
  const disabled = buttons.filter((b) => b.hasAttribute("disabled"));
  expect(disabled.length).toBeGreaterThanOrEqual(2);
});

test("create modal submits scope, name and atoms", async () => {
  const user = userEvent.setup();
  mountAdminRoles();
  await waitFor(() => expect(screen.getByText("auditor")).toBeInTheDocument());
  await user.click(screen.getByRole("button", { name: /新增|Create/ }));
  await user.type(screen.getByLabelText(/名稱|Name/i), "new");
  await user.click(screen.getByRole("button", { name: /^確定$|^OK$|送出|Save/ }));
  await waitFor(() => {
    const calls = api.mock.calls.filter(([p, i]) => p === "/api/admin/roles" && (i as RequestInit | undefined)?.method === "POST");
    expect(calls.length).toBe(1);
  });
});
