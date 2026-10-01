import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import ExplorePanel from "../ExplorePanel";
import { stubFetch } from "../../testing/stubFetch";
import { ARTIFACT_TABLES } from "../../testing/artifactTables";

// The lazy graph chunk failing to load (stale deploy, offline) must stay
// inside the graph area: the mode switch and the table mode keep working.
// Separate file: the module mock below makes every import of GraphView fail.
vi.mock("../GraphView", () => {
  throw new Error("Failed to fetch dynamically imported module");
});

stubFetch(async (path: string) =>
  new Response(
    JSON.stringify(path === "/api/artifact-tables" ? ARTIFACT_TABLES : { rows: [], total: 0, stale: false }),
    { status: 200 },
  ));
beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); });

test("a failed graph chunk shows the error in place; table mode still works", async () => {
  render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <ExplorePanel projectId="p1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByText("圖譜"));
  expect(await screen.findByText("此區塊載入失敗")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /^重\s?試$/ })).toBeInTheDocument();
  await user.click(screen.getByText("資料表"));
  expect(screen.queryByText("此區塊載入失敗")).not.toBeInTheDocument();
  expect(screen.getByText("資料表")).toBeInTheDocument();
});
