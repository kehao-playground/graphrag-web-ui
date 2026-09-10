import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Navigate, Route, Routes } from "react-router-dom";
import ProjectDetail, { ProjectPane } from "../ProjectDetail";
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
const VIEWER_PERMS = ["project:view"];

// Mutable: the atom-split tests swap my_permissions before their render.
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

// HealthOut fixture (Task 4) — the sidebar's badge source. Zero counts and
// no active job keep the sidebar quiet by default.
const HEALTH = {
  active_job: null,
  artifacts_stale: false,
  files: { indexed: 0, modified: 0, new: 0, removed: 0, skipped: 0, total: 0 },
  has_baseline: false,
  ingest_check: "unavailable_no_baseline",
  last_index: null,
  latest_run: null,
};

// FileListOut fixture: draft.md (modified) is the ?state=new,modified row
// the routed deep link must surface; notes.txt (indexed) proves filtering.
const FILES = {
  files: [
    { name: "notes.txt", size: 1024, modified_at: "2026-08-19T00:00:00Z",
      sha256: "aa", index_state: "indexed", tags: ["policy"] },
    { name: "draft.md", size: 512, modified_at: "2026-08-19T01:00:00Z",
      sha256: "bb", index_state: "modified", tags: [] },
  ],
  usage_bytes: 1536, quota_bytes: 10240,
  ingest_check: "available", has_baseline: true,
};

// Mutable: badge tests swap counts before their render.
let healthBody: Record<string, unknown> = HEALTH;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

// Real detailOf stays under test; only the transport is mocked. Every URL
// the layout, the sidebar and the routed panes can hit is enumerated, so
// a wrong endpoint fails loudly instead of falling through to a
// same-shaped body.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (url: string, init?: RequestInit) => {
    if (url === "/api/roles?scope=project") return json(projectRoles);
    if (url === "/api/projects/p1/members" && init?.method !== "PUT") return json(members);
    if (url === "/api/projects/p1/members/u3" && init?.method === "PUT") return json({});
    if (url === "/api/users") return json(users);
    if (url === "/api/projects/p1/health") return json(healthBody);
    if (url === "/api/projects/p1/files") return json(FILES);
    if (url === "/api/projects/p1/tags") return json({ tags: [] });
    if (url === "/api/projects/p1/jobs") return json([]);
    if (url === "/api/projects/p1/jobs/preflight") return json({
      active_job: null, cache_bytes: 0, cache_quota_mb: 1024,
      disk_free_mb: 51200, disk_watermark_mb: 1024, last_run: null,
    });
    if (url === "/api/projects/p1/question-sets") return json({ sets: [] });
    if (url === "/api/projects/p1/test-runs") return json({ runs: [], rows: [] });
    if (url === "/api/projects/p1/settings") return json({ content: "input:\n  type: text\n", content_hash: "h1" });
    if (url === "/api/projects/p1/settings/versions") return json([]);
    if (url === "/api/projects/p1/env") return json({ keys: [] });
    if (url === "/api/projects/p1") return json(project);
    throw new Error(`unexpected ${init?.method ?? "GET"} ${url}`);
  }),
}))

// Mounts the routed tree exactly as App wires it (Task 5): the ProjectDetail
// layout with its nested panes, entered at a deep-linkable route.
// myPermissions and health seed the two fixtures the routed UI branches on.
function renderApp(opts: {
  route?: string;
  myPermissions?: string[];
  health?: Record<string, unknown>;
} = {}) {
  project.my_permissions = opts.myPermissions ?? OWNER_PERMS;
  healthBody = opts.health
    ? {
        ...HEALTH,
        ...opts.health,
        files: { ...HEALTH.files, ...((opts.health.files ?? {}) as object) },
      }
    : HEALTH;
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[opts.route ?? "/projects/p1"]}>
        <Routes>
          <Route path="/projects/:id" element={<ProjectDetail />}>
            <Route index element={<Navigate to="overview" replace />} />
            <Route path="overview" element={<ProjectPane pane="overview" />} />
            <Route path="files" element={<ProjectPane pane="files" />} />
            <Route path="jobs" element={<ProjectPane pane="jobs" />} />
            <Route path="tests" element={<ProjectPane pane="tests" />} />
            <Route path="explore" element={<ProjectPane pane="explore" />} />
            <Route path="settings" element={<ProjectPane pane="settings" />} />
            <Route path="members" element={<ProjectPane pane="members" />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  // alice is a plain user here — with RBAC v2 her reach comes from the
  // project's my_permissions atoms, not from any role she holds globally
  project.my_permissions = OWNER_PERMS;
  healthBody = HEALTH;
  useAuth.setState({
    user: { id: "u1", email: "alice@test.local", display_name: "Alice",
            roles: [], permissions: [], is_active: true, must_change_password: false },
  });
})

// --- The routed panes (Task 5): panes are routes, not tab state ---

test("a deep link lands on the right pane", async () => {
  renderApp({ route: "/projects/p1/tests" })
  expect(await screen.findByRole("heading", { name: "檢索測試工作台" })).toBeInTheDocument()
  expect(screen.queryByRole("heading", { name: "文件" })).not.toBeInTheDocument()
})

test("/projects/:id redirects to the overview pane", async () => {
  renderApp({ route: "/projects/p1" })
  expect(await screen.findByRole("heading", { name: "總覽" })).toBeInTheDocument()
  // the members table lives on its own pane now — the redirect did not land there
  expect(screen.queryByText("bob@test.local")).not.toBeInTheDocument()
})

test("a reload keeps the pane", async () => {
  const { unmount } = renderApp({ route: "/projects/p1/settings" })
  unmount()
  renderApp({ route: "/projects/p1/settings" })
  expect(await screen.findByRole("heading", { name: "設定" })).toBeInTheDocument()
})

test("entries missing an atom are hidden, not disabled", async () => {
  renderApp({ route: "/projects/p1/overview", myPermissions: VIEWER_PERMS })
  expect(await screen.findByRole("heading", { name: "總覽" })).toBeInTheDocument()
  // project:edit_settings / project:manage absent → the entries are removed outright
  expect(screen.queryByRole("link", { name: "設定" })).not.toBeInTheDocument()
  expect(screen.queryByRole("link", { name: "成員" })).not.toBeInTheDocument()
  // project:view alone still reaches the knowledge-base panes
  expect(screen.getByRole("link", { name: /文件/ })).toBeInTheDocument()
})

test("sidebar badges come from /health, not from a client-side count", async () => {
  renderApp({ route: "/projects/p1/overview", health: { files: { new: 2, modified: 1 } } })
  expect(await screen.findByText("3")).toBeInTheDocument()
})

test("the jobs badge marks a running job", async () => {
  renderApp({ route: "/projects/p1/overview", health: { active_job: { id: "j1", type: "index" } } })
  // the link exists before /health resolves; the badge is what arrives late
  const jobsLink = await screen.findByRole("link", { name: /任務/ })
  await waitFor(() => expect(jobsLink.textContent).toContain("1"))
})

test("the files entry links to the state filter the overview uses", async () => {
  renderApp({ route: "/projects/p1/files?state=new,modified" })
  expect(await screen.findByText("draft.md")).toBeInTheDocument()
  expect(screen.queryByText("notes.txt")).not.toBeInTheDocument()
})

// --- Existing behavior, now driven through the routed panes ---

test("owner row stays labeled and locked; add flow submits the picked role_id", async () => {
  renderApp({ route: "/projects/p1/members" })

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

test("maintainer atoms: no member management, jobs launchable, files editable, settings read-only", async () => {
  const { unmount } = renderApp({ route: "/projects/p1/members", myPermissions: MAINTAINER_PERMS })

  expect(await screen.findByText("alice@test.local")).toBeInTheDocument()
  // project:manage is absent → the add-member bar is gone and role selects lock
  expect(screen.queryByTitle("新增成員")).toBeNull()
  const bobRow = screen.getByText("bob@test.local").closest("tr")!
  expect(within(bobRow).getByRole("combobox")).toBeDisabled()

  // project:run_jobs is present → indexing is launchable from the sidebar entry
  fireEvent.click(screen.getByRole("link", { name: /任務/ }))
  expect(await screen.findByRole("button", { name: "開始索引" })).not.toBeDisabled()

  // project:edit_content is present → the upload area renders from the sidebar entry
  fireEvent.click(screen.getByRole("link", { name: /文件/ }))
  expect(await screen.findByText("點擊或拖曳檔案上傳")).toBeInTheDocument()

  // project:edit_settings is absent → the settings entry is hidden, not
  // disabled; the pane itself stays URL-reachable and renders read-only
  expect(screen.queryByRole("link", { name: "設定" })).not.toBeInTheDocument()
  unmount()
  renderApp({ route: "/projects/p1/settings", myPermissions: MAINTAINER_PERMS })
  expect(await screen.findByRole("button", { name: "儲存設定" })).toBeDisabled()
})
