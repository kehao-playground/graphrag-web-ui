import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AdminAudit from "../AdminAudit";
import { useAuth } from "../../stores/auth";

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

const { api } = vi.hoisted(() => ({
  api: vi.fn(async (path: string) => {
    const url = new URL(path, "http://x");
    const action = url.searchParams.get("action");
    const rows = action ? ROWS.filter((r) => r.action === action) : ROWS;
    return new Response(JSON.stringify({ rows, total: rows.length }), { status: 200 });
  }),
}));
vi.mock("../../api/client", () => ({ api, detailOf: async () => "err" }));

beforeEach(() => {
  api.mockClear();
  useAuth.setState({
    authMode: "local",
    accessToken: "t",
    user: { id: "me", email: "me@b.c", roles: [], permissions: ["users:manage"] },
  } as never);
});

function mountAudit() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><AdminAudit /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders the log with actions, targets and payloads", async () => {
  mountAudit();
  await waitFor(() => expect(screen.getByText("file.uploaded")).toBeInTheDocument());
  expect(screen.getByText("user.created")).toBeInTheDocument();
  expect(screen.getByText("env.key_set")).toBeInTheDocument();
  // The payload is what makes a row meaningful — the action alone is not.
  expect(screen.getByText(/notes\.md/)).toBeInTheDocument();
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
  await waitFor(() => expect(api).toHaveBeenCalled());
  const url = new URL(api.mock.calls[0][0] as string, "http://x");
  expect(url.searchParams.get("limit")).toBe("50");
  expect(url.searchParams.get("offset")).toBe("0");
});

test("the action filter is sent to the server, not applied client-side", async () => {
  const user = userEvent.setup();
  mountAudit();
  await waitFor(() => expect(screen.getByText("env.key_set")).toBeInTheDocument());

  await user.type(screen.getByLabelText("依動作篩選"), "user.created{enter}");
  await waitFor(() => {
    const filtered = api.mock.calls
      .map(([p]) => new URL(p as string, "http://x"))
      .filter((u) => u.searchParams.get("action") === "user.created");
    expect(filtered.length).toBeGreaterThan(0);
  });
  // Server-side paging means the row count must come back from the request,
  // so the other actions are gone rather than merely hidden.
  await waitFor(() => expect(screen.queryByText("env.key_set")).not.toBeInTheDocument());
});
