import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter } from "react-router-dom";
import AdminRoles from "../AdminRoles";
import { useAuth } from "../../stores/auth";
import { stubFetch } from "../../testing/stubFetch";

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
    if (path === "/api/admin/roles/c0" && init?.method === "PATCH") {
      return new Response(null, { status: 204 });
    }
    throw new Error("unexpected " + path);
  }),
}));
stubFetch(api);

beforeEach(() => {
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] },
  } as never);
});

function mountAdminRoles() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter><AdminRoles /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("lists the catalog in the reader's language; built-ins carry no actions", async () => {
  mountAdminRoles();
  // Built-ins render their catalog label and description, never the id (R4-18).
  await waitFor(() => expect(screen.getByText("使用者管理員")).toBeInTheDocument());
  expect(screen.queryByText("user_admin")).toBeNull();
  expect(screen.getByText("管理使用者與角色")).toBeInTheDocument();
  expect(screen.getByText("全域")).toBeInTheDocument();
  expect(screen.getByText("專案")).toBeInTheDocument();
  // custom roles keep their own name
  const customRow = screen.getByText("auditor").closest("tr")!;
  const builtinRow = screen.getByText("使用者管理員").closest("tr")!;
  expect(within(customRow).getByRole("button", { name: /編\s*輯/ })).toBeInTheDocument();
  expect(within(builtinRow).queryByRole("button")).toBeNull();
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

// R1-113: the edit modal is open exactly while a role is targeted, and
// saving patches that role.
test("edit opens the modal with the role's values and patches that role", async () => {
  const user = userEvent.setup();
  mountAdminRoles();
  const row = (await screen.findByText("auditor")).closest("tr")!;
  await user.click(within(row).getByRole("button", { name: /編\s*輯/ }));
  const dialog = await screen.findByRole("dialog");
  const name = within(dialog).getByLabelText(/名稱/);
  expect(name).toHaveValue("auditor");
  await user.clear(name);
  await user.type(name, "auditor2");
  await user.click(within(dialog).getByRole("button", { name: /^確\s*定$|^OK$/ }));
  await waitFor(() => {
    const calls = api.mock.calls.filter(([p, i]) => p === "/api/admin/roles/c0" && (i as RequestInit | undefined)?.method === "PATCH");
    expect(calls.length).toBe(1);
    expect(JSON.parse(String((calls[0][1] as RequestInit).body)).name).toBe("auditor2");
  });
});
