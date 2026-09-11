import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import AnswerView from "../tests/AnswerView";
import type { Citation } from "../../api/types";
import type * as ApiClient from "../../api/client";

// Same mock discipline as FilesPanel.test.tsx: branch by URL so a wrong-path
// query starves the assertion instead of passing on another call's payload.
// Real detailOf stays under test; only the transport is mocked.
let filesBody: Record<string, unknown> = {};
let previewCalls = 0;
let lastPreviewBody: unknown = null;
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/files") {
    return new Response(JSON.stringify(filesBody), { status: 200 });
  }
  if (path === "/api/projects/p1/files/file-a/preview") {
    previewCalls += 1;
    lastPreviewBody = init?.body ? JSON.parse(init.body as string) : null;
    return new Response(JSON.stringify({
      text: "PREVIEW-BODY", offset: 3, total_size: 12, match: true,
    }), { status: 200 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string, RequestInit?]),
}));

// The citation fixtures (spec §7.4): only Sources entries carry a
// source_name, and it is resolved WITH the answer — the component must
// never look one up later.
const SOURCES_WITH_NAME: Citation = {
  label: "Sources",
  ids: [7],
  entries: [{ id: 7, text: "the cited passage", source_name: "file-a" }],
};
const SOURCES_WITHOUT_NAME: Citation = {
  label: "Sources",
  ids: [8],
  entries: [{ id: 8, text: "an unlinked passage", source_name: null }],
};
// Non-Sources labels summarize many documents; even a stray source_name
// must not render as a link (the backend withholds it, the UI agrees).
const ENTITIES_CITATION: Citation = {
  label: "Entities",
  ids: [1],
  entries: [{ id: 1, text: "an entity entry", source_name: "file-a" }],
};

// Every name a test may cite. removedNames drops names from the live file
// listing — exactly how the component learns a cited document is gone.
const ALL_NAMES = ["file-a", "file-b"];

function renderAnswer(opts: {
  citations?: Citation[];
  origin?: { resultId: string } | null;
  removedNames?: string[];
} = {}) {
  filesBody = {
    files: ALL_NAMES
      .filter((name) => !opts.removedNames?.includes(name))
      .map((name) => ({
        name, size: 8, modified_at: "2026-09-01T00:00:00Z", sha256: "aa",
        index_state: "indexed", tags: [],
      })),
    usage_bytes: 16, quota_bytes: 1024, ingest_check: "available", has_baseline: true,
  };
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AnswerView
          projectId="p1"
          answer="答案內容"
          citations={opts.citations ?? []}
          timings={null}
          origin={opts.origin}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

// The citations block renders collapsed (as in production); entries mount
// only after the header click — the AdhocQuery tests expand it the same way.
async function expandCitations() {
  const user = userEvent.setup();
  await user.click(await screen.findByText(/引用 \(/));
  return user;
}

beforeEach(() => {
  previewCalls = 0;
  lastPreviewBody = null;
});

test("a Sources citation with a source_name is clickable", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME] });
  const user = await expandCitations();
  await user.click(await screen.findByRole("button", { name: "file-a" }));
  expect(await screen.findByText("PREVIEW-BODY")).toBeInTheDocument();
});

test("a stored run passes the historic locator", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], origin: { resultId: "r1" } });
  const user = await expandCitations();
  await user.click(await screen.findByRole("button", { name: "file-a" }));
  await screen.findByText("PREVIEW-BODY");
  expect(lastPreviewBody).toEqual({ result_id: "r1", entry_id: 7 });
});

test("an ad-hoc answer passes the passage locator", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], origin: null });
  const user = await expandCitations();
  await user.click(await screen.findByRole("button", { name: "file-a" }));
  await screen.findByText("PREVIEW-BODY");
  expect(lastPreviewBody).toEqual({ passage: "the cited passage" });
});

test("a null source_name renders unlinked, with the answer still shown", async () => {
  renderAnswer({ citations: [SOURCES_WITHOUT_NAME] });
  await expandCitations();
  expect(await screen.findByText(/答案內容/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /file-/ })).not.toBeInTheDocument();
});

test("non-Sources labels are never linked", async () => {
  renderAnswer({ citations: [ENTITIES_CITATION] });
  await expandCitations();
  expect(await screen.findByText(/^Entities #1/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /file-/ })).not.toBeInTheDocument();
});

test("a deleted source renders disabled and does not call preview", async () => {
  renderAnswer({ citations: [SOURCES_WITH_NAME], removedNames: ["file-a"] });
  await expandCitations();
  const el = await screen.findByText("file-a");
  expect(el.closest("button")).toBeDisabled();
  expect(previewCalls).toBe(0);
  // Hover the Tooltip's wrapper span: antd disables pointer events on the
  // button itself, so in a real browser the hover lands on the span around
  // it — which is where the tooltip listens.
  await userEvent.hover(el.closest("button")!.parentElement!);
  expect(await screen.findByText(/文件已刪除/)).toBeInTheDocument();
  expect(previewCalls).toBe(0);
});
