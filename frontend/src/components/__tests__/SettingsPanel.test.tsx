import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import { Link, RouterProvider, createMemoryRouter } from "react-router-dom";
import SettingsPanel from "../SettingsPanel";
import { stubFetch } from "../../testing/stubFetch";

const FIXTURE = {
  content: "input:\n  type: text\n  file_pattern: '.*\\.md$$'\n",
  content_hash: "hash-from-get",
};
// The shape graphrag init writes (trimmed): the model blocks sit two levels
// deep, which is what form mode has to walk (R4-02).
const INIT_YAML = [
  "completion_models:",
  "  default_completion_model:",
  "    model_provider: openai",
  "    model: gpt-4.1",
  "    auth_method: api_key",
  "    api_key: ${GRAPHRAG_API_KEY}",
  "embedding_models:",
  "  default_embedding_model:",
  "    model_provider: openai",
  "    model: text-embedding-3-large",
  "    auth_method: api_key",
  "chunking:",
  "  size: 1200",
  "  overlap: 100",
  "",
].join("\n");

// Mutable per test (reset in beforeEach) so a test can swap the server side.
const fixture = { ...FIXTURE };
let envKeys: Array<{ key: string; masked: string; is_placeholder: boolean }> = [];
let activeJob: { id: string; type: string } | null = null;

// Same mock discipline as FilesPanel.test.tsx: branch by URL (and method for
// PUT) so a wrong endpoint or body cannot silently pass on another call.
let versionsTotal = 0;
let putResponse: () => Response = () => new Response(JSON.stringify({ content_hash: "new" }), { status: 200 });
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (path === "/api/projects/p1/settings" && init?.method !== "PUT") {
    return new Response(JSON.stringify(fixture), { status: 200 });
  }
  if (path === "/api/projects/p1/settings" && init?.method === "PUT") {
    return putResponse();
  }
  if (path.startsWith("/api/projects/p1/settings/versions?")) {
    return new Response(JSON.stringify({ items: [], total: versionsTotal }), { status: 200 });
  }
  if (path === "/api/projects/p1/env") {
    return new Response(JSON.stringify({ keys: envKeys }), { status: 200 });
  }
  if (path === "/api/projects/p1/jobs/preflight") {
    return new Response(JSON.stringify({ active_job: activeJob }), { status: 200 });
  }
  if (path === "/api/projects/p1/dry-run") {
    return new Response(JSON.stringify({ ok: true, output: "" }), { status: 200 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});

beforeEach(() => {
  versionsTotal = 0;
  Object.assign(fixture, FIXTURE);
  envKeys = [];
  activeJob = null;
  putResponse = () => new Response(JSON.stringify({ content_hash: "new" }), { status: 200 });
  apiMock.mockClear();
});
// The real api client stays under test; only fetch is stubbed.
stubFetch(apiMock);

// A data router (useBlocker needs one), with a second route to leave to.
function mount({ canEdit = true }: { canEdit?: boolean } = {}) {
  const router = createMemoryRouter([
    { path: "/settings", element: <><Link to="/elsewhere">leave</Link><SettingsPanel projectId="p1" canEdit={canEdit} /></> },
    { path: "/elsewhere", element: <p>elsewhere</p> },
  ], { initialEntries: ["/settings"] });
  render(
    <QueryClientProvider client={createQueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

async function loadedYaml() {
  const ta = await screen.findByRole("textbox", { name: /yaml/i });
  await waitFor(() => expect(ta).toHaveValue(fixture.content));
  return ta;
}

// Makes the draft dirty (Save is disabled on a clean draft, R4-22).
async function editYaml(suffix = "# edit\n") {
  const ta = await loadedYaml();
  fireEvent.change(ta, { target: { value: fixture.content + suffix } });
  return fixture.content + suffix;
}

const saveButton = () => screen.getByRole("button", { name: /儲存設定/ });

test("yaml mode renders fetched content in the textarea", async () => {
  mount();
  await loadedYaml();
});

test("save invokes api with PUT and body {content, expected_hash}", async () => {
  mount();
  const user = userEvent.setup();
  const edited = await editYaml();
  await user.click(saveButton());
  await waitFor(() =>
    expect(apiMock).toHaveBeenCalledWith("/api/projects/p1/settings", expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({ content: edited, expected_hash: "hash-from-get" }),
    })));
});

test("a settings_conflict 409 opens the conflict modal showing server content", async () => {
  putResponse = () => new Response(
    JSON.stringify({ detail: "conflict", code: "settings_conflict",
                     current_content: "server: 1\n", current_hash: "hash-on-disk" }),
    { status: 409 });
  mount();
  const user = userEvent.setup();
  await editYaml();
  await user.click(saveButton());
  expect(await screen.findByText("server: 1")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /覆\s?寫/ })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "重新載入" })).toBeInTheDocument();
});

test("a project_indexing 409 is a toast, not the conflict modal, and keeps the draft (R4-01)", async () => {
  putResponse = () => new Response(
    JSON.stringify({ detail: "project is indexing", code: "project_indexing", params: { job_type: "index" } }), { status: 409 });
  mount();
  const user = userEvent.setup();
  const edited = await editYaml();
  await user.click(saveButton());
  expect(await screen.findByText("索引任務正在進行，文件與設定異動已暫停")).toBeInTheDocument();
  expect(screen.queryByText("設定已被他人修改")).not.toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: /yaml/i })).toHaveValue(edited);
});

test("a running index shows the frozen notice and disables every write (R4-01)", async () => {
  activeJob = { id: "j1", type: "index" };
  envKeys = [{ key: "GRAPHRAG_API_KEY", masked: "sk****", is_placeholder: false }];
  mount();
  expect(await screen.findByText(/索引任務進行中/)).toBeInTheDocument();
  await editYaml();
  expect(saveButton()).toBeDisabled();
  expect(screen.getByRole("button", { name: /^設\s?定$/ })).toBeDisabled();
  expect(screen.getByRole("button", { name: /刪\s?除/ })).toBeDisabled();
});

test("Save stays disabled until the draft differs from the server copy (R4-22)", async () => {
  mount();
  await loadedYaml();
  expect(saveButton()).toBeDisabled();
  await editYaml();
  expect(saveButton()).toBeEnabled();
  expect(screen.getByText("尚未儲存的變更")).toBeInTheDocument();
});

test("leaving with unsaved edits asks first; cancel stays, discard leaves (R4-22)", async () => {
  const router = mount();
  const user = userEvent.setup();
  const edited = await editYaml();
  await user.click(screen.getByRole("link", { name: "leave" }));
  const dialog = await screen.findByRole("dialog");
  expect(within(dialog).getByText("放棄尚未儲存的變更？")).toBeInTheDocument();
  await user.click(within(dialog).getByRole("button", { name: "繼續編輯" }));
  expect(router.state.location.pathname).toBe("/settings");
  expect(screen.getByRole("textbox", { name: /yaml/i })).toHaveValue(edited);

  await user.click(screen.getByRole("link", { name: "leave" }));
  await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "放棄變更" }));
  expect(await screen.findByText("elsewhere")).toBeInTheDocument();
});

test("a clean draft leaves without asking", async () => {
  mount();
  await loadedYaml();
  await userEvent.setup().click(screen.getByRole("link", { name: "leave" }));
  expect(await screen.findByText("elsewhere")).toBeInTheDocument();
});

test("form mode shows the configured models from the init yaml (R4-02, R1-82)", async () => {
  fixture.content = INIT_YAML;
  mount();
  await loadedYaml();
  await userEvent.setup().click(screen.getByText("表單"));
  expect(await screen.findByDisplayValue("gpt-4.1")).toBeInTheDocument();
  expect(screen.getByDisplayValue("text-embedding-3-large")).toBeInTheDocument();
  expect(screen.getAllByDisplayValue("openai")).toHaveLength(2);
  expect(screen.getAllByDisplayValue("api_key")).toHaveLength(2);
  expect(screen.getByLabelText("chunk-size")).toHaveValue("1200");
});

test("form mode saves chunk sizes as integers, not strings (R4-02)", async () => {
  fixture.content = INIT_YAML;
  mount();
  await loadedYaml();
  const user = userEvent.setup();
  await user.click(screen.getByText("表單"));
  const size = await screen.findByLabelText("chunk-size");
  await user.clear(size);
  await user.type(size, "900");
  await user.click(saveButton());
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
    "/api/projects/p1/settings", expect.objectContaining({ method: "PUT" })));
  const put = apiMock.mock.calls.find(([, init]) => init?.method === "PUT")!;
  const sent = JSON.parse(String(put[1]!.body)).content as string;
  expect(sent).toMatch(/^ {2}size: 900$/m);
  expect(sent).not.toContain("'900'");
  expect(sent).toMatch(/^ {4}model: gpt-4\.1$/m);
});

test("form mode does not crash on empty base yaml and shows a degraded notice", async () => {
  fixture.content = "";
  fixture.content_hash = "empty-base";
  mount();
  await screen.findByRole("button", { name: /儲存設定/ });
  await userEvent.setup().click(screen.getByText("表單"));
  expect(await screen.findByText(/無法以表單編輯/)).toBeInTheDocument();
});

test("form draft is reset when the server content hash changes", async () => {
  fixture.content = "chunking:\n  size: 10\n";
  fixture.content_hash = "h1";
  // a successful PUT swaps the server state the panel refetches
  putResponse = () => {
    fixture.content = "chunking:\n  size: 42\n";
    fixture.content_hash = "h2";
    return new Response(JSON.stringify({ content_hash: "h2" }), { status: 200 });
  };
  mount();
  await loadedYaml();
  const user = userEvent.setup();
  await user.click(screen.getByText("表單"));
  const size = await screen.findByLabelText("chunk-size");
  await waitFor(() => expect(size).toHaveValue("10"));
  await user.clear(size);
  await user.type(size, "99");
  expect(size).toHaveValue("99");
  await user.click(saveButton());
  // refetch lands h2/42: the stale draft (99) must NOT survive the hash change
  await waitFor(() => expect(screen.getByLabelText("chunk-size")).toHaveValue("42"));
});

test("a network failure on save is shown, not swallowed (R1-21)", async () => {
  putResponse = () => { throw new TypeError("Failed to fetch"); };
  mount();
  const user = userEvent.setup();
  await editYaml();
  await user.click(saveButton());
  expect(await screen.findByText("Failed to fetch")).toBeInTheDocument();
});

test("the init placeholder key is called out, not shown as configured (R4-23)", async () => {
  envKeys = [{ key: "GRAPHRAG_API_KEY", masked: "<A****", is_placeholder: true }];
  mount();
  expect(await screen.findByText(/GRAPHRAG_API_KEY 尚未設定/)).toBeInTheDocument();
  // translated headers, and the key section sits above the YAML editor
  const header = screen.getByRole("columnheader", { name: "名稱" });
  expect(screen.getByRole("columnheader", { name: "值（部分隱藏）" })).toBeInTheDocument();
  const ta = await loadedYaml();
  expect(header.compareDocumentPosition(ta) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
});

test("deleting a key asks for confirmation first (R4-23)", async () => {
  envKeys = [{ key: "OTHER_KEY", masked: "ke****", is_placeholder: false }];
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /刪\s?除/ }));
  expect(apiMock).not.toHaveBeenCalledWith(
    "/api/projects/p1/env/OTHER_KEY", expect.objectContaining({ method: "DELETE" }));
  const pop = await screen.findByRole("tooltip");
  expect(within(pop).getByText(/刪除 OTHER_KEY/)).toBeInTheDocument();
  await user.click(within(pop).getByRole("button", { name: /刪\s?除/ }));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
    "/api/projects/p1/env/OTHER_KEY", expect.objectContaining({ method: "DELETE" })));
});

test("the settings check says what it does not test (R4-24)", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /檢查設定/ }));
  expect(await screen.findByText(/不會呼叫模型/)).toBeInTheDocument();
});

test("an invalid-YAML save stays inline with its line, not a parser-dump toast (R4-35)", async () => {
  const reason = 'while parsing a flow sequence\n  in "<unicode string>", line 2, column 9\n'
    + "expected ',' or ']', but got '<stream end>'\n  in \"<unicode string>\", line 3, column 1";
  putResponse = () => new Response(JSON.stringify({
    detail: `invalid yaml: ${reason}`, code: "settings_invalid_yaml", params: { reason },
  }), { status: 400 });
  mount();
  const user = userEvent.setup();
  await editYaml("bad: [1, 2\n");
  await user.click(saveButton());
  const alert = (await screen.findByText(/第 3 行，第 1 欄/)).closest<HTMLElement>(".ant-alert")!;
  expect(within(alert).queryByText(/unicode string/)).not.toBeInTheDocument();
  // go-to-line selects the offending line in the editor
  await user.click(within(alert).getByRole("button", { name: "跳至該行" }));
  const ta = screen.getByRole("textbox", { name: /yaml/i }) as HTMLTextAreaElement;
  const lineStart = ta.value.split("\n").slice(0, 2).join("\n").length + 1;
  await waitFor(() => expect(ta.selectionStart).toBe(lineStart));
  expect(ta).toHaveFocus();
});

test("the version history pages instead of stopping at 50 (R3-10)", async () => {
  versionsTotal = 25;
  mount();
  await loadedYaml();
  await userEvent.click(await screen.findByTitle("2"));
  await waitFor(() => expect(apiMock).toHaveBeenCalledWith(
    "/api/projects/p1/settings/versions?limit=20&offset=20", expect.anything(),
  ));
});
