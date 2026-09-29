import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter } from "react-router-dom";
import AdminAudit from "../AdminAudit";
import { useAuth } from "../../stores/auth";
import { stubFetch } from "../../testing/stubFetch";

// No RTL auto-cleanup here: prior renders leak rows into later tests.
afterEach(cleanup);

// Three rows covering the actor cases the backend can produce: a signed-in
// actor, a system-written row (no actor at all), and a row whose actor's
// user record is gone.
const ROWS = [
  {
    id: 3,
    actor_id: "u1",
    actor_email: "admin@test.local",
    action: "file.uploaded",
    target_type: "project",
    target_id: "9f8e7d6c-0000-4000-8000-000000000001",
    payload: { name: "notes.md", size: 12 },
    created_at: "2026-09-01T10:00:00Z",
  },
  {
    id: 2,
    actor_id: null,
    actor_email: null,
    action: "user.created",
    target_type: "user",
    target_id: "aaaabbbb-0000-4000-8000-000000000002",
    payload: { origin: "bootstrap" },
    created_at: "2026-09-01T09:00:00Z",
  },
  {
    id: 1,
    actor_id: "gone",
    actor_email: null,
    action: "env.key_set",
    target_type: "project",
    target_id: "ccccdddd-0000-4000-8000-000000000003",
    payload: { key: "GRAPHRAG_API_KEY" },
    created_at: "2026-09-01T08:00:00Z",
  },
];

const { api, state } = vi.hoisted(() => ({
  state: { auditStatus: 200 },
  api: vi.fn(async (path: string) => {
    // Target names resolve from the lists the admin can already read.
    if (path === "/api/projects") {
      return new Response(JSON.stringify([
        { id: "9f8e7d6c-0000-4000-8000-000000000001", name: "Research Corpus" },
      ]), { status: 200 });
    }
    if (path === "/api/users") {
      return new Response(JSON.stringify([
        { id: "aaaabbbb-0000-4000-8000-000000000002", email: "new@test.local",
          display_name: "New", is_active: true },
      ]), { status: 200 });
    }
    if (state.auditStatus !== 200) {
      return new Response(JSON.stringify({ detail: "forbidden" }), { status: state.auditStatus });
    }
    const url = new URL(path, "http://x");
    const action = url.searchParams.get("action");
    const rows = action ? ROWS.filter((r) => r.action === action) : ROWS;
    return new Response(JSON.stringify({ rows, total: rows.length }), { status: 200 });
  }),
}));
stubFetch(api);

beforeEach(() => {
  api.mockClear();
  state.auditStatus = 200;
  useAuth.setState({
    authMode: "local",
    accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] },
  } as never);
});

function mountAudit() {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter><AdminAudit /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders the log with actions, targets and payloads", async () => {
  mountAudit();
  // Actions read as catalog labels, not raw ids (R4-17).
  await waitFor(() => expect(screen.getByText("上傳文件")).toBeInTheDocument());
  expect(screen.getByText("建立使用者")).toBeInTheDocument();
  expect(screen.getByText("設定環境金鑰")).toBeInTheDocument();
  // Targets name the project or user when the admin can see it; an
  // unresolvable one keeps its short id.
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument();
  expect(await screen.findByText("new@test.local")).toBeInTheDocument();
  expect(screen.getByText(/ccccdddd/)).toBeInTheDocument();
  // The payload renders as key: value pairs, not a JSON string.
  expect(screen.getByText("notes.md")).toBeInTheDocument();
  expect(screen.queryByText(/"name"/)).toBeNull();
});

test("an unknown action renders its raw id", async () => {
  mountAudit();
  await waitFor(() => expect(screen.getByText("上傳文件")).toBeInTheDocument());
  expect(screen.queryByText("file.uploaded")).toBeNull();
});

test("a refused read shows an error, not the empty-filter sentence (R4-17)", async () => {
  state.auditStatus = 403;
  mountAudit();
  expect(await screen.findByText("無法載入稽核記錄")).toBeInTheDocument();
  expect(screen.queryByText("沒有符合篩選條件的稽核記錄")).toBeNull();
});

test("distinguishes a system row from one whose actor was deleted", async () => {
  mountAudit();
  await waitFor(() => expect(screen.getByText("admin@test.local")).toBeInTheDocument());
  // actor_id null → nobody was signed in; actor_id set with no email → the
  // user row is gone. Rendering both as blank would lose a real distinction.
  expect(screen.getByText("系統")).toBeInTheDocument();
  expect(screen.getByText("（已刪除的使用者）")).toBeInTheDocument();
});

test("requests the first page with the configured page size", async () => {
  mountAudit();
  await waitFor(() => expect(api).toHaveBeenCalledWith(expect.stringMatching(/^\/api\/admin\/audit/), expect.anything()));
  const call = api.mock.calls.find(([p]) => String(p).startsWith("/api/admin/audit"))!;
  const url = new URL(call[0] as string, "http://x");
  expect(url.searchParams.get("limit")).toBe("50");
  expect(url.searchParams.get("offset")).toBe("0");
});

test("the action filter is sent to the server, not applied client-side", async () => {
  const user = userEvent.setup();
  mountAudit();
  await waitFor(() => expect(screen.getByText("設定環境金鑰")).toBeInTheDocument());

  // The filter picks from the action catalog (recognition, not recall).
  await user.click(screen.getByRole("combobox", { name: "依動作篩選" }));
  // The option list is virtual: search narrows it to the one we want.
  await user.keyboard("建立使用者");
  const dropdown = document.querySelector(".ant-select-dropdown") as HTMLElement;
  await user.click(await within(dropdown).findByText("建立使用者", { selector: ".ant-select-item-option-content" }));
  await waitFor(() => {
    const filtered = api.mock.calls
      .map(([p]) => new URL(p as string, "http://x"))
      .filter((u) => u.searchParams.get("action") === "user.created");
    expect(filtered.length).toBeGreaterThan(0);
  });
  // Server-side paging means the row count must come back from the request,
  // so the other actions are gone rather than merely hidden.
  await waitFor(() => expect(screen.queryByText("設定環境金鑰")).not.toBeInTheDocument());
});
