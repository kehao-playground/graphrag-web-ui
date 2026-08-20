import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import SettingsPanel from "../SettingsPanel";

const FIXTURE = {
  content: "input:\n  type: text\n  file_pattern: '.*\\.md$$'\n",
  content_hash: "hash-from-get",
};

// Same mock discipline as FilesPanel.test.tsx: branch by URL (and method for
// PUT) so a wrong endpoint or body cannot silently pass on another call.
let putResponse: () => Response = () => new Response(JSON.stringify({ content_hash: "new" }), { status: 200 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/settings" && init?.method !== "PUT") {
    return new Response(JSON.stringify(FIXTURE), { status: 200 });
  }
  if (path === "/api/projects/p1/settings" && init?.method === "PUT") {
    return putResponse();
  }
  if (path === "/api/projects/p1/settings/versions") {
    return new Response(JSON.stringify([]), { status: 200 });
  }
  if (path === "/api/projects/p1/env") {
    return new Response(JSON.stringify({ keys: [] }), { status: 200 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
vi.mock("../../api/client", () => ({ api: (...args: unknown[]) => apiMock(...args as [string, RequestInit?]) }));

function mount() {
  const qc = new QueryClient();
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <SettingsPanel projectId="p1" canEdit />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

test("yaml mode renders fetched content in the textarea", async () => {
  mount();
  const ta = await screen.findByRole("textbox", { name: /yaml/i });
  await waitFor(() => expect(ta).toHaveValue(FIXTURE.content));
});

test("save invokes api with PUT and body {content, expected_hash}", async () => {
  mount();
  const ta = await screen.findByRole("textbox", { name: /yaml/i });
  await waitFor(() => expect(ta).toHaveValue(FIXTURE.content));
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /儲存/ }));
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/settings", {
      method: "PUT",
      body: JSON.stringify({ content: FIXTURE.content, expected_hash: "hash-from-get" }),
    }));
});

test("a 409 response opens the conflict modal showing server content", async () => {
  putResponse = () => new Response(
    JSON.stringify({ detail: "conflict", current_content: "server: 1\n", current_hash: "hash-on-disk" }),
    { status: 409 });
  mount();
  const ta = await screen.findByRole("textbox", { name: /yaml/i });
  await waitFor(() => expect(ta).toHaveValue(FIXTURE.content));
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /儲存/ }));
  expect(screen.getByRole("button", { name: /覆\s?寫/ })).toBeInTheDocument();
  expect(await screen.findByText("server: 1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新載入" })).toBeInTheDocument();
});
