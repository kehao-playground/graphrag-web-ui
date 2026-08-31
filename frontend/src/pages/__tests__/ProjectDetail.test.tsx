import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectDetail from "../ProjectDetail";
import { useAuth } from "../../stores/auth";
import { api } from "../../api/client";
import type * as ApiClient from "../../api/client";

// The backend seed's four project built-ins, in the catalog's (scope, name) order.
const VIEWER_ID = "00000000-0000-4000-8000-000000000003";
const MAINTAINER_ID = "00000000-0000-4000-8000-000000000004";
const EDITOR_ID = "00000000-0000-4000-8000-000000000005";
const OWNER_ID = "00000000-0000-4000-8000-000000000006";
const projectRoles = [
  { id: EDITOR_ID, scope: "project", name: "editor", description: "",
    permissions: ["project:view", "project:edit_content", "project:run_jobs", "project:edit_settings"],
    is_system: true },
  { id: MAINTAINER_ID, scope: "project", name: "maintainer", description: "",
    permissions: ["project:view", "project:edit_content", "project:run_jobs"],
    is_system: true },
  { id: OWNER_ID, scope: "project", name: "owner", description: "",
    permissions: ["project:view", "project:edit_content", "project:run_jobs", "project:edit_settings", "project:manage"],
    is_system: true },
  { id: VIEWER_ID, scope: "project", name: "viewer", description: "",
    permissions: ["project:view"], is_system: true },
];

const OWNER_PERMS = ["project:view", "project:edit_content", "project:run_jobs", "project:edit_settings", "project:manage"];
const MAINTAINER_PERMS = ["project:view", "project:edit_content", "project:run_jobs"];

// Mutable: the atom-split test swaps my_permissions before its render.
const project = {
  id: "p1", name: "P1", slug: "p1", description: null,
  input_file_type: "text", owner_id: "u1", created_at: "2026-01-01T00:00:00Z",
  my_permissions: OWNER_PERMS,
};
const members = [
  { user_id: "u1", email: "alice@test.local", display_name: "Alice",
    role_id: OWNER_ID, role_name: "owner" },
  { user_id: "u2", email: "bob@test.local", display_name: "Bob",
    role_id: VIEWER_ID, role_name: "viewer" },
];
const users = [
  { id: "u1", email: "alice@test.local", display_name: "Alice", is_active: true },
  { id: "u2", email: "bob@test.local", display_name: "Bob", is_active: true },
  { id: "u3", email: "carol@test.local", display_name: "Carol", is_active: true },
];

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

// Real detailOf stays under test; only the transport is mocked. Every URL
// the page and its tab panels can hit is enumerated, so a wrong endpoint
// fails loudly instead of falling through to a same-shaped body.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/roles?scope=project") return json(projectRoles);
    if (url === "/api/projects/p1/members" && init?.method !== "PUT") return json(members);
    if (url === "/api/projects/p1/members/u3" && init?.method === "PUT") return json({});
    if (url === "/api/users") return json(users);
    if (url === "/api/projects/p1/files") return json({ files: [], usage_bytes: 0, quota_bytes: 1048576 });
    if (url === "/api/projects/p1/jobs") return json([]);
    if (url === "/api/projects/p1/jobs/preflight") return json({
      active_job: null, cache_bytes: 0, cache_quota_mb: 1024,
      disk_free_mb: 51200, disk_watermark_mb: 1024, last_run: null,
    });
    if (url === "/api/projects/p1/settings") return json({ content: "input:\n  type: text\n", content_hash: "h1" });
    if (url === "/api/projects/p1/settings/versions") return json([]);
    if (url === "/api/projects/p1/env") return json({ keys: [] });
    if (url === "/api/projects/p1") return json(project);
    throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
  }),
}))

function mountProjectDetail() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/projects/p1"]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  // alice is a plain user here — with RBAC v2 her reach comes from the
  // project's my_permissions atoms, not from any role she holds globally
  project.my_permissions = OWNER_PERMS;
  useAuth.setState({
    user: { id: "u1", email: "alice@test.local", display_name: "Alice",
            roles: [], permissions: [], is_active: true, must_change_password: false },
  });
})

test("owner row stays labeled and locked; add flow submits the picked role_id", async () => {
  mountProjectDetail()

  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  expect(screen.getByText("bob@test.local")).toBeInTheDocument()
  // owner stays visible in its members-table row (single-owner policy: locked row)
  const aliceRow = screen.getByText("alice@test.local").closest("tr")!
  expect(within(aliceRow).getByText("擁有者")).toBeInTheDocument()

  // add-member role select: defaults to the catalog's first grantable option
  const addBar = screen.getByTitle("新增成員")
  const roleSelect = (await within(addBar).findByText("編輯者")).closest(".ant-select")!
  const combobox = roleSelect.querySelector('input[role="combobox"]')!
  fireEvent.mouseDown(combobox)
  await waitFor(() => {
    const opts = Array.from(document.querySelectorAll(".ant-select-item-option-content"))
      .map((o) => o.textContent)
    // owner is not grantable; the catalog orders by (scope, name)
    expect(opts).toEqual(["編輯者", "維護者", "檢視者"])
  })

  // pick maintainer by label, pick carol, submit → the PUT carries role_id
  fireEvent.click(await screen.findByText("維護者"))
  const userSelect = screen.getByText("選擇使用者").closest(".ant-select")!
  fireEvent.mouseDown(userSelect.querySelector('input[role="combobox"]')!)
  fireEvent.click(await screen.findByText("Carol(carol@test.local)"))
  fireEvent.click(within(addBar).getByRole("button", { name: /新\s*增/ }))

  await waitFor(() => expect(vi.mocked(api)).toHaveBeenCalledWith(
    "/api/projects/p1/members/u3",
    { method: "PUT", body: JSON.stringify({ role_id: MAINTAINER_ID }) },
  ))
})

test("maintainer atoms: no member management, files/jobs editable, settings read-only", async () => {
  project.my_permissions = MAINTAINER_PERMS
  mountProjectDetail()

  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  // project:manage is absent → the add-member bar is gone and role selects lock
  expect(screen.queryByTitle("新增成員")).toBeNull()
  const bobRow = screen.getByText("bob@test.local").closest("tr")!
  expect(within(bobRow).getByRole("combobox")).toBeDisabled()

  // project:edit_settings is absent → the settings tab stays read-only
  fireEvent.click(screen.getByRole("tab", { name: "設定" }))
  expect(await screen.findByRole("button", { name: "儲存設定" })).toBeDisabled()

  // project:run_jobs is present → indexing is launchable
  fireEvent.click(screen.getByRole("tab", { name: "任務" }))
  expect(await screen.findByRole("button", { name: "開始索引" })).not.toBeDisabled()

  // project:edit_content is present → the upload area renders
  fireEvent.click(screen.getByRole("tab", { name: "檔案" }))
  expect(await screen.findByText("點擊或拖曳檔案上傳")).toBeInTheDocument()
})
