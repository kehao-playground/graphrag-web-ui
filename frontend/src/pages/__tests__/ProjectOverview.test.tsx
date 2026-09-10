import { render, screen } from "@testing-library/react";
import { vi, beforeEach } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectOverview from "../ProjectOverview";
import { nextAction } from "../../components/project/nextAction";
import type { ActionHealth } from "../../components/project/nextAction";
import type * as ApiClient from "../../api/client";

// --- nextAction: one ordered check, unit-tested without rendering ---

// Defaults describe a clean, baselined project; each test overrides only
// the fields its rule reads (plan Task 6, spec §9.3).
const H = (over: Partial<ActionHealth> = {}): ActionHealth => ({
  active_job: null,
  artifacts_stale: false,
  files: { new: 0, modified: 0, removed: 0, skipped: 0 },
  has_baseline: true,
  ingest_check: "available",
  latest_run: null,
  ...over,
});

test("an active job outranks everything", () => {
  expect(nextAction(H({ active_job: { id: "j1", type: "index" }, files: { ...H().files, removed: 3 } })).key)
    .toBe("activeJob");
});

test("missing output under an existing baseline outranks a missing baseline check", () => {
  expect(nextAction(H({ ingest_check: "unavailable_not_indexed" })).key).toBe("artifactsMissing");
});

test("stale artifacts from a failed attempt reach the same card", () => {
  expect(nextAction(H({ artifacts_stale: true })).key).toBe("artifactsMissing");
});

test("removed outranks new and modified", () => {
  expect(nextAction(H({ files: { ...H().files, removed: 1, new: 4, modified: 2 } })).key)
    .toBe("removed");
});

test("a project whose only problem is deleted documents is NOT healthy", () => {
  expect(nextAction(H({ files: { ...H().files, removed: 1 } })).key).not.toBe("healthy");
});

test("skipped ranks below new and modified", () => {
  expect(nextAction(H({ files: { ...H().files, new: 1, skipped: 3 } })).key).toBe("stale");
  expect(nextAction(H({ files: { ...H().files, skipped: 3 } })).key).toBe("skipped");
});

test("regressions are the last non-healthy card", () => {
  expect(nextAction(H({ latest_run: { regressions: 2 } })).key).toBe("regressions");
});

test("a clean project is healthy", () => {
  expect(nextAction(H()).key).toBe("healthy");
});

test("no baseline asks for a full index and says an update will not do", () => {
  const a = nextAction(H({ has_baseline: false }));
  expect(a.key).toBe("noBaseline");
});

// --- The card that ladder picks, rendered through ProjectOverview ---

// Full HealthOut fixture: the stat tiles read files.total, last_index and
// latest_run.ratings, which the pure ladder never touches.
const CLEAN = {
  active_job: null,
  artifacts_stale: false,
  files: { indexed: 5, modified: 0, new: 0, removed: 0, skipped: 0, total: 5 },
  has_baseline: true,
  ingest_check: "available",
  last_index: { job_id: "j0", type: "index", finished_at: "2026-09-04T00:00:00Z" },
  latest_run: null,
};

let healthBody: Record<string, unknown> = CLEAN;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

// Same mock discipline as ProjectDetail.test: only the one URL the
// overview may hit is enumerated, so a wrong endpoint fails loudly.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (url: string) => {
    if (url === "/api/projects/p1/health") return json(healthBody);
    throw new Error(`unexpected GET ${url}`);
  }),
}));

// Renders the overview alone at its routed URL, so the action card's
// links carry the absolute hrefs the real router would produce.
function renderOverview(over: {
  artifacts_stale?: boolean;
  files?: Partial<(typeof CLEAN)["files"]>;
  ingest_check?: string;
} = {}) {
  healthBody = { ...CLEAN, ...over, files: { ...CLEAN.files, ...(over.files ?? {}) } };
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/projects/p1/overview"]}>
        <Routes>
          <Route path="/projects/:id/overview" element={<ProjectOverview projectId="p1" />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  healthBody = CLEAN;
});

test("the stale-artifacts card explains that citation links are off", async () => {
  renderOverview({ artifacts_stale: true });
  expect(await screen.findByText(/引用連結會暫時關閉/)).toBeInTheDocument();
});

test("the stale card links to files with the filter applied", async () => {
  renderOverview({ files: { new: 2, modified: 1 } });
  const link = await screen.findByRole("link", { name: /查看待索引文件/ });
  expect(link).toHaveAttribute("href", "/projects/p1/files?state=new,modified");
});

test("unavailable title_column adds a caveat without changing the ranking", async () => {
  renderOverview({ ingest_check: "unavailable_title_column", files: { removed: 1 } });
  expect(await screen.findByText(/靜默略過偵測已關閉/)).toBeInTheDocument();
  expect(screen.getByText(/只有完整重建/)).toBeInTheDocument();
});
