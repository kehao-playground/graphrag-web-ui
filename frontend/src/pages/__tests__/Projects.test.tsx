import { render, screen } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import Projects from "../Projects";

// mock 必須按 URL 分流:所有呼叫回同一陣列的話,/api/users 拿到的也是
// project 陣列 — owner_id 永遠對不上,測試結構上抓不到查找 key 的錯
vi.mock("../../api/client", () => ({
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
}))

test("renders project list with owner resolved by owner_id", async () => {
  const qc = new QueryClient()
  render(<QueryClientProvider client={qc}><MemoryRouter><Projects /></MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText("Research Corpus")).toBeInTheDocument()
  // 擁有者欄:project.owner_id=u1 → /api/users 裡對應的 user,不是「—」
  expect(await screen.findByText("owner@example.com")).toBeInTheDocument()
})
