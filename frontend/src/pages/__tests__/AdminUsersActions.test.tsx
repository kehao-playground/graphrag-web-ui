import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { createQueryClient } from "../../api/queryClient";
import AdminUsers from "../AdminUsers";
import { useAuth } from "../../stores/auth";
import { stubFetch } from "../../testing/stubFetch";

// The write paths of the users page (R2-35): create, edit, disable, reset,
// each with the coded error the backend answers when it refuses.

afterEach(cleanup);

const userAdmin = {
  id: "00000000-0000-4000-8000-000000000001", scope: "global", name: "user_admin",
  description: "Manage users and roles", permissions: ["users:manage"], is_system: true,
};
const users = [
  { id: "me", email: "me@test.local", display_name: "Me", roles: [userAdmin],
    permissions: ["users:manage"], is_active: true, must_change_password: false },
  { id: "u2", email: "bob@test.local", display_name: "Bob", roles: [],
    permissions: [], is_active: true, must_change_password: false },
];

type Reply = { status: number; body?: unknown };
// Per-test answers for the write calls; reads always succeed unless
// listFails is set.
let writes: Record<string, Reply>;
let listFails: boolean;
const calls: { method: string; url: string; body: unknown }[] = [];

const json = (status: number, body: unknown = {}) =>
  new Response(status === 204 ? null : JSON.stringify(body), { status });

stubFetch(vi.fn(async (url: string, init?: RequestInit) => {
  const method = init?.method ?? "GET";
  if (method === "GET") {
    if (url === "/api/roles?scope=global") return json(200, [userAdmin]);
    if (listFails) return json(500, { detail: "boom" });
    return json(200, users);
  }
  calls.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : undefined });
  const reply = writes[`${method} ${url}`] ?? { status: 200 };
  return json(reply.status, reply.body);
}));

beforeEach(() => {
  writes = {};
  listFails = false;
  calls.length = 0;
  useAuth.setState({
    authMode: "local", accessToken: "t",
    user: { id: "me", email: "me@test.local", roles: [], permissions: ["users:manage"] } as never,
  });
});

function mount() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter><AdminUsers /></MemoryRouter>
    </QueryClientProvider>,
  );
}

const rowOf = async (email: string) =>
  (await screen.findByText(email)).closest("tr") as HTMLElement;

async function fillCreate() {
  await userEvent.click(screen.getByRole("button", { name: "建立使用者" }));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("電子郵件"), "carol@test.local");
  await userEvent.type(within(dialog).getByLabelText("顯示名稱"), "Carol");
  await userEvent.type(within(dialog).getByLabelText("初始密碼"), "carol-pass-1");
  await userEvent.click(within(dialog).getByRole("button", { name: /建\s*立/ }));
}

test("a failed list read shows the error alert", async () => {
  listFails = true;
  mount();
  expect(await screen.findByText("無法載入使用者列表")).toBeInTheDocument();
});

test("create posts the form and confirms", async () => {
  mount();
  await screen.findByText("bob@test.local");
  await fillCreate();
  expect(await screen.findByText("使用者已建立")).toBeInTheDocument();
  expect(calls).toEqual([{
    method: "POST", url: "/api/admin/users",
    body: { email: "carol@test.local", display_name: "Carol", password: "carol-pass-1", roles: [] },
  }]);
});

test("create refused as email_registered shows the catalog sentence and keeps the dialog", async () => {
  writes["POST /api/admin/users"] = {
    status: 409, body: { detail: "email already registered", code: "email_registered" },
  };
  mount();
  await screen.findByText("bob@test.local");
  await fillCreate();
  expect(await screen.findByText("此電子郵件已被註冊")).toBeInTheDocument();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

test("editing your own row never submits roles", async () => {
  mount();
  await userEvent.click(within(await rowOf("me@test.local")).getByRole("button", { name: /編\s*輯/ }));
  const dialog = await screen.findByRole("dialog");
  const name = within(dialog).getByLabelText("顯示名稱");
  await userEvent.clear(name);
  await userEvent.type(name, "Me Again");
  await userEvent.click(within(dialog).getByRole("button", { name: /儲\s*存/ }));
  expect(await screen.findByText("已更新使用者")).toBeInTheDocument();
  expect(calls).toEqual([
    { method: "PATCH", url: "/api/admin/users/me", body: { display_name: "Me Again" } },
  ]);
});

test("your own disable button is locked; disabling another user can be refused", async () => {
  writes["PATCH /api/admin/users/u2"] = {
    status: 400,
    body: { detail: "last user manager", code: "last_user_manager_protected" },
  };
  mount();
  expect(within(await rowOf("me@test.local")).getByRole("button", { name: /停\s*用/ })).toBeDisabled();
  await userEvent.click(within(await rowOf("bob@test.local")).getByRole("button", { name: /停\s*用/ }));
  const confirm = await screen.findByText("停用 bob@test.local？");
  const pop = confirm.closest(".ant-popover") as HTMLElement;
  await userEvent.click(within(pop).getByRole("button", { name: /停\s*用/ }));
  expect(await screen.findByText("不能移除最後一位使用者管理者")).toBeInTheDocument();
  expect(calls).toEqual([
    { method: "PATCH", url: "/api/admin/users/u2", body: { is_active: false } },
  ]);
});

test("reset password posts the new password and confirms", async () => {
  mount();
  await userEvent.click(within(await rowOf("bob@test.local")).getByTestId("reset-password-button"));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("新密碼"), "bob-new-pass");
  await userEvent.click(within(dialog).getByRole("button", { name: /重\s*設/ }));
  expect(await screen.findByText("密碼已重設")).toBeInTheDocument();
  expect(calls).toEqual([
    { method: "POST", url: "/api/admin/users/u2/reset-password", body: { new_password: "bob-new-pass" } },
  ]);
});

test("a too-short reset password is refused before sending", async () => {
  mount();
  await userEvent.click(within(await rowOf("bob@test.local")).getByTestId("reset-password-button"));
  const dialog = await screen.findByRole("dialog");
  await userEvent.type(within(dialog).getByLabelText("新密碼"), "short");
  await userEvent.click(within(dialog).getByRole("button", { name: /重\s*設/ }));
  expect(await screen.findByText("密碼至少 8 字元")).toBeInTheDocument();
  await waitFor(() => expect(calls).toEqual([]));
});
