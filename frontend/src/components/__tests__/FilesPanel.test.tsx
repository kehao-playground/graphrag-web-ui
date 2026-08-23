import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import FilesPanel from "../FilesPanel";
import type * as ApiClient from "../../api/client";

// Same mock discipline as Projects.test.tsx: branch by URL so a lookup-key
// mistake (wrong endpoint) cannot silently pass on another call's payload.
// Real bodyOf/detailOf stay under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (path: string) => {
    // Route by URL like Projects.test.tsx: the FilesOut fixture is only
    // served on the /files endpoint, so a wrong-path query starves the panel.
    if (path === "/api/projects/p1/files") {
      return new Response(JSON.stringify({
        files: [
          { name: "notes.txt", size: 1024, modified_at: "2026-08-19T00:00:00Z" },
          { name: "readme.md", size: 512, modified_at: "2026-08-19T01:00:00Z" },
        ],
        usage_bytes: 1536,
        quota_bytes: 10240,
      }), { status: 200 });
    }
    return new Response(JSON.stringify({}), { status: 200 });
  }),
}));

test("renders file names and quota percent from GET files", async () => {
  const qc = new QueryClient()
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <FilesPanel projectId="p1" inputFileType="text" canEdit />
      </MemoryRouter>
    </QueryClientProvider>,
  )
  expect(await screen.findByText("notes.txt")).toBeInTheDocument()
  expect(screen.getByText("readme.md")).toBeInTheDocument()
  // 1536 / 10240 = 15%
  expect(screen.getByText("15%")).toBeInTheDocument()
})
