import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { RouterProvider, createMemoryRouter } from "react-router-dom";
import { load } from "js-yaml";
import { createQueryClient } from "../../api/queryClient";
import SettingsPanel from "../SettingsPanel";
import { stubFetch } from "../../testing/stubFetch";

// Spy on the parser itself: form mode must parse the document once per
// content change, not once per render (R1-115) — a keystroke in a form
// field re-renders the panel but leaves the YAML untouched.
vi.mock("js-yaml", async (importOriginal) => {
  const real = await importOriginal<typeof import("js-yaml")>();
  return { ...real, load: vi.fn(real.load) };
});

const YAML = [
  "completion_models:",
  "  default_completion_model:",
  "    model: gpt-4.1",
  "chunking:",
  "  size: 1200",
  "",
].join("\n");

stubFetch(vi.fn(async (path: string) => {
  if (path === "/api/projects/p1/settings") {
    return new Response(JSON.stringify({ content: YAML, content_hash: "h" }), { status: 200 });
  }
  if (path.startsWith("/api/projects/p1/settings/versions?")) {
    return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
  }
  if (path === "/api/projects/p1/env") return new Response(JSON.stringify({ keys: [] }), { status: 200 });
  return new Response(JSON.stringify({ active_job: null }), { status: 200 });
}));

test("typing in a form field does not re-parse the YAML (R1-115)", async () => {
  const router = createMemoryRouter(
    [{ path: "/", element: <SettingsPanel projectId="p1" canEdit /> }],
  );
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await screen.findByLabelText("settings-yaml");
  await user.click(screen.getByText(/表單|Form/));
  const model = await screen.findByDisplayValue("gpt-4.1");
  await user.type(model, "x");
  const afterFirst = vi.mocked(load).mock.calls.length;
  await user.type(model, "-mini");
  expect(screen.getByDisplayValue("gpt-4.1x-mini")).toBeInTheDocument();
  expect(vi.mocked(load).mock.calls.length).toBe(afterFirst);
});
