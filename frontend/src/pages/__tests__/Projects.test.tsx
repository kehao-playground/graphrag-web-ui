import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";
import { stubFetch } from "../../testing/stubFetch";

// The mock must branch by URL: if every call returned the same array,
// /api/users would get the project array too — owner_id would never match,
// and the test could not structurally catch a wrong lookup key.
// The real api client stays under test; only fetch is stubbed.
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
    const status = (healthBody.__status as number | undefined) ?? 200;
    return new Response(JSON.stringify(healthBody), { status });
  }
  return new Response(JSON.stringify(projectsBody), { status: 200 });
});
stubFetch(apiMock);
// Three visible projects: p2 is the one with pending documents (2 new + 1
// modified); the others are clean so the health column stays quiet for them.
// zh-TW: that pending count renders as 3 份待索引.
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
    last_attempt: { status: "succeeded", finished_at: "2026-09-01T00:00:00Z" } as
      { status: string; finished_at: string },
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
    <QueryClientProvider client={createQueryClient()}>
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
  expect(await screen.findByText("3 份待索引")).toBeInTheDocument();
  expect(healthRequests).toEqual([
    "/api/projects/health?ids=p1,p2,p3",
  ]);
});

test("a project with only removed documents is flagged, not shown healthy", async () => {
  renderProjects({ health: { p1: { files: { removed: 2, new: 0, modified: 0, skipped: 0 } } } });
  expect(await screen.findByText(/已刪除文件仍在索引中/)).toBeInTheDocument();
});

// R3-06: the newest finished index failed — flagged, not read as fresh.
test("a project whose last index attempt failed is flagged; a cancel is not", async () => {
  healthBody = {
    projects: {
      ...HEALTH_BODY.projects,
      p1: { ...clean(), last_attempt: { status: "failed", finished_at: "2026-09-02T00:00:00Z" } },
      p3: { ...clean(), last_attempt: { status: "cancelled", finished_at: "2026-09-02T00:00:00Z" } },
    },
  };
  renderProjects();
  expect(await screen.findAllByText("最近一次索引失敗")).toHaveLength(1);
});

test("more than 200 projects ask for health in chunks of 200 (R3-35)", async () => {
  projectsBody = Array.from({ length: 201 }, (_, i) => ({
    ...PROJECTS_BODY[0], id: `q${i}`, name: `Project ${i}`,
  }));
  healthBody = { projects: {} };
  renderProjects();
  expect(await screen.findByText("Project 200")).toBeInTheDocument();
  await waitFor(() => expect(healthRequests).toHaveLength(2));
  expect(healthRequests[0].split("=")[1].split(",")).toHaveLength(200);
  expect(healthRequests[1]).toBe("/api/projects/health?ids=q200");
});

test("a failed health batch says so in the column (R3-35)", async () => {
  healthBody = { __status: 500 };
  renderProjects();
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument();
  expect(await screen.findAllByText("健康狀態無法取得")).toHaveLength(3);
});
