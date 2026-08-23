import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";
import type * as ApiClient from "../../api/client";

// The mock must branch by URL: if every call returned the same array,
// /api/users would get the project array too — owner_id would never match,
// and the test could not structurally catch a wrong lookup key
// Real detailOf stays under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: vi.fn(async (path: string) => {
    if (path === "/api/users") {
      return new Response(JSON.stringify([
        { id: "u1", email: "owner@example.com", display_name: "Owner", is_active: true },
        { id: "u2", email: "other@example.com", display_name: "Other", is_active: true },
      ]), { status: 200 });
    }
    return new Response(JSON.stringify([
      { id: "p1", name: "Research Corpus", slug: "research-corpus", description: null,
        input_file_type: "text", owner_id: "u1", created_at: "2026-08-19T00:00:00Z" },
    ]), { status: 200 });
  }),
}));

test("renders project list with owner resolved by owner_id", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><Projects /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument()
  // Owner column: project.owner_id=u1 → the matching /api/users user, not the "—" placeholder
  expect(await screen.findByText("owner@example.com")).toBeInTheDocument()
})
