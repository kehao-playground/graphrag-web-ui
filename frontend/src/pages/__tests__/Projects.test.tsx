import { render, screen } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";
import type * as ApiClient from "../../api/client";

// The mock must branch by URL: if every call returned the same array,
// /api/users would get the project array too — owner_id would never match,
// and the test could not structurally catch a wrong lookup key.
// Real detailOf stays under test; only the transport is mocked.
const healthRequests: string[] = [];
const apiMock = vi.fn(async (path: string) => {
  if (path === "/api/users") {
    return new Response(JSON.stringify([
      { id: "u1", email: "owner@example.com", display_name: "Owner", is_active: true },
      { id: "u2", email: "other@example.com", display_name: "Other", is_active: true },
    ]), { status: 200 });
  }
  // The one batch call the list issues (spec §7.5): captured so the tests
  // can pin BOTH the URL shape and that nothing else was requested.
  if (path.startsWith("/api/projects/health")) {
    healthRequests.push(path);
    return new Response(JSON.stringify(healthBody), { status: 200 });
  }
  return new Response(JSON.stringify(projectsBody), { status: 200 });
});
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string]),
}));
// Three visible projects: p2 is the one with pending documents (2 new + 1
// modified); the others are clean so the health column stays quiet for them.
// zh-TW: that pending count renders as 3 待索引.
const PROJECTS_BODY = [
  { id: "p1", name: "Research Corpus", slug: "research-corpus", description: null,
    input_file_type: "text", owner_id: "u1", created_at: "2026-08-19T00:00:00Z" },
  { id: "p2", name: "客服知識庫", slug: "kb", description: null,
    input_file_type: "text", owner_id: "u2", created_at: "2026-08-20T00:00:00Z" },
  { id: "p3", name: "Manuals", slug: "manuals", description: null,
    input_file_type: "text", owner_id: "u2", created_at: "2026-08-21T00:00:00Z" },
];

// BatchHealthOut (Task 4) as far as the list column cares. Mutable so the
// removed-only test can override p1; reset per test for order independence.
const HEALTH_BODY = {
  projects: {
    p1: clean(),
    p2: { ...clean(), files: { new: 2, modified: 1, removed: 0, skipped: 0 } },
    p3: clean(),
  },
};

// A BatchHealthEntryOut with no faults: no stale artifacts, a baseline,
// ingest check available, all file counts zero.
function clean() {
  return {
    artifacts_stale: false, has_baseline: true, ingest_check: "available",
    last_index: { finished_at: "2026-09-01T00:00:00Z" },
    files: { new: 0, modified: 0, removed: 0, skipped: 0 },
  };
}

let projectsBody = PROJECTS_BODY;
let healthBody: Record<string, unknown> = HEALTH_BODY as unknown as Record<string, unknown>;

beforeEach(() => {
  healthRequests.length = 0;
  projectsBody = PROJECTS_BODY;
  healthBody = HEALTH_BODY as unknown as Record<string, unknown>;
});

function renderProjects(opts: {
  health?: Record<string, { files: { new: number; modified: number; removed: number; skipped: number } }>;
} = {}) {
  if (opts.health) {
    healthBody = {
      projects: Object.fromEntries(
        Object.entries(HEALTH_BODY.projects).map(([pid, entry]) => {
          const over = opts.health?.[pid];
          return over ? [pid, { ...(entry as object), files: over.files }] : [pid, entry];
        }),
      ),
    };
  }
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter><Projects /></MemoryRouter>
    </QueryClientProvider>,
  );
}

test("renders project list with owner resolved by owner_id", async () => {
  renderProjects();
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument();
  // Owner column: project.owner_id=u1 → the matching /api/users user, not the "—" placeholder
  expect(await screen.findByText("owner@example.com")).toBeInTheDocument();
});

test("project list shows index health from one batch request", async () => {
  renderProjects();
  await screen.findByText("客服知識庫");
  expect(await screen.findByText("3 待索引")).toBeInTheDocument();
  expect(healthRequests).toEqual([
    "/api/projects/health?ids=p1,p2,p3",
  ]);
});

test("a project with only removed documents is flagged, not shown healthy", async () => {
  renderProjects({ health: { p1: { files: { removed: 2, new: 0, modified: 0, skipped: 0 } } } });
  expect(await screen.findByText(/已刪除文件仍在索引中/)).toBeInTheDocument();
});
