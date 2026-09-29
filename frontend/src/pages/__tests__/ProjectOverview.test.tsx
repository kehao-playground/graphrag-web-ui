import { render, screen } from "@testing-library/react";
import { vi, beforeEach } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import ProjectOverview from "../ProjectOverview";
import { nextAction } from "../../components/project/nextAction";
import type { ActionHealth } from "../../components/project/nextAction";
import { stubFetch } from "../../testing/stubFetch";
import { i18n } from "../../i18n";

// --- nextAction: one ordered check, unit-tested without rendering ---

// Defaults describe a clean, baselined project; each test overrides only
// the fields its rule reads (plan Task 6, spec §9.3).
const H = (over: Partial<ActionHealth> = {}): ActionHealth => ({
  active_job: null,
  api_key_missing: false,
  artifacts_stale: false,
  files: { new: 0, modified: 0, removed: 0, skipped: 0, total: 5 },
  has_baseline: true,
  ingest_check: "available",
  last_index: { job_id: "j0" },
  last_attempt: { job_id: "j0", status: "succeeded" },
  latest_run: null,
  ...over,
});

test("an active job outranks everything and opens that job's log", () => {
  const a = nextAction(H({
    active_job: { id: "j1", type: "index" }, api_key_missing: true, files: { ...H().files, removed: 3 },
  }));
  expect(a.key).toBe("activeJob");
  expect(a.target).toBe("jobs?log=j1");
});

// Test runs are off the jobs page (decision D2); the workbench shows them.
test("a running test run's card leads to the workbench", () => {
  expect(nextAction(H({ active_job: { id: "t1", type: "test_run" } })).target).toBe("tests");
});

// R4-04: every project starts empty; the first step is uploading, not a
// full index of nothing, and it is not an error.
test("a project without documents asks for an upload, as info", () => {
  const a = nextAction(H({
    files: { ...H().files, total: 0 }, has_baseline: false, last_index: null, last_attempt: null,
    api_key_missing: true,
  }));
  expect(a).toEqual({ key: "noDocuments", severity: "info", target: "files" });
});

// F9-01: a placeholder key fails the index at its first model call.
test("a missing API key ranks right after documents, before any index advice", () => {
  const a = nextAction(H({
    api_key_missing: true, has_baseline: false, last_index: null, last_attempt: null,
  }));
  expect(a).toEqual({ key: "apiKeyMissing", severity: "warning", target: "settings" });
  expect(nextAction(H({ api_key_missing: true, artifacts_stale: true })).key).toBe("apiKeyMissing");
});

test("no baseline is info before any index ran, an error after one failed", () => {
  const none = { has_baseline: false, last_index: null, last_attempt: null };
  expect(nextAction(H(none)).severity).toBe("info");
  // R3-06: a failed first index has no last_index (successes only) — the
  // attempt is what says an index ran.
  const failed = { ...none, last_attempt: { job_id: "j1", status: "failed" } };
  expect(nextAction(H(failed)).severity).toBe("error");
});

// R4-05: the skipped card leads to the evidence (the run's log), not to a
// file filter with nothing on it to review.
test("the skipped card targets the last index run's log", () => {
  expect(nextAction(H({ files: { ...H().files, skipped: 2 } })).target).toBe("jobs?log=j0");
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
  api_key_missing: false,
  artifacts_stale: false,
  files: { indexed: 5, modified: 0, new: 0, removed: 0, skipped: 0, total: 5 },
  has_baseline: true,
  ingest_check: "available",
  last_index: { job_id: "j0", type: "index", finished_at: "2026-09-04T00:00:00Z" },
  last_attempt: {
    job_id: "j0", type: "index", status: "succeeded", finished_at: "2026-09-04T00:00:00Z",
  } as Record<string, string>,
  latest_run: null,
};

let healthBody: Record<string, unknown> = CLEAN;

const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });

// Same mock discipline as ProjectDetail.test: only the one URL the
// overview may hit is enumerated, so a wrong endpoint fails loudly.
const api = vi.fn(async (url: string) => {
  if (url === "/api/projects/p1/health") return json(healthBody);
  throw new Error(`unexpected GET ${url}`);
});
stubFetch(api);

// Renders the overview alone at its routed URL, so the action card's
// links carry the absolute hrefs the real router would produce.
function renderOverview(over: {
  api_key_missing?: boolean;
  artifacts_stale?: boolean;
  files?: Partial<(typeof CLEAN)["files"]>;
  ingest_check?: string;
  last_attempt?: Record<string, string>;
  active_job?: Record<string, string>;
} = {}) {
  healthBody = { ...CLEAN, ...over, files: { ...CLEAN.files, ...(over.files ?? {}) } };
  return render(
    <QueryClientProvider client={createQueryClient()}>
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

test("the skipped card links to the last run's log first, then the files", async () => {
  renderOverview({ files: { skipped: 2, indexed: 3 } });
  expect(await screen.findByText(/編碼/)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /查看上次索引的日誌/ }))
    .toHaveAttribute("href", "/projects/p1/jobs?log=j0");
  expect(screen.getByRole("link", { name: /查看未收錄文件/ }))
    .toHaveAttribute("href", "/projects/p1/files?state=skipped");
});

test("an empty project's card is an info upload prompt", async () => {
  renderOverview({ files: { indexed: 0, total: 0 } });
  const link = await screen.findByRole("link", { name: /前往文件/ });
  expect(link).toHaveAttribute("href", "/projects/p1/files");
  expect(link.closest(".ant-alert")).toHaveClass("ant-alert-info");
});

test("a placeholder API key sends the user to settings", async () => {
  renderOverview({ api_key_missing: true });
  expect(await screen.findByRole("link", { name: /前往設定/ }))
    .toHaveAttribute("href", "/projects/p1/settings");
});

// R3-06: a failure newer than the last index is shown as an attempt, in
// red, next to the index it did not replace.
test("a failed attempt after the last index is shown apart from it", async () => {
  renderOverview({
    last_attempt: {
      job_id: "j9", type: "index", status: "failed", finished_at: "2026-09-05T00:00:00Z",
    },
  });
  const line = await screen.findByText(/最近一次嘗試：失敗/);
  expect(line.closest(".ant-typography")).toHaveClass("ant-typography-danger");
});

test("a succeeded last attempt adds no attempt line", async () => {
  renderOverview();
  await screen.findByText(/最近一次索引/);
  expect(screen.queryByText(/最近一次嘗試/)).not.toBeInTheDocument();
});

test("English copy names the running job and the last index in one sentence each (R4-19)", async () => {
  await i18n.changeLanguage("en-US");
  renderOverview({ active_job: { id: "j1", type: "update" }, files: { new: 1, total: 6 } });
  expect(await screen.findByText("An update job is running; health updates when it finishes."))
    .toBeInTheDocument();
  // The last-index line uses the short type label ("Index, finished …").
  expect(screen.getByText(/^Index, finished /)).toBeInTheDocument();
  // A tile shows the bare count under its label, not "6 documents".
  expect(screen.getByText("6")).toBeInTheDocument();
  await i18n.changeLanguage("zh-TW");
});

test("stat tiles share the row evenly instead of wrapping ragged (R4-32)", async () => {
  renderOverview();
  const label = await screen.findByText("文件總數");
  expect(label.closest(".ant-card")).toHaveStyle({ flex: "1 1 200px" });
});
