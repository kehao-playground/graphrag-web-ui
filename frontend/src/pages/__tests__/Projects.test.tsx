import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";

vi.mock("../../api/client", () => ({
  api: vi.fn().mockResolvedValue(new Response(JSON.stringify([
    { id: "p1", name: "Research Corpus", slug: "research-corpus", description: null,
      input_file_type: "text", owner_id: "u1", created_at: "2026-08-19T00:00:00Z" },
  ]), { status: 200 })),
}))

test("renders project list", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><Projects /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument()
})
