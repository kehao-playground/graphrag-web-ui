import { render, screen, cleanup, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import AdhocQuery from "../tests/AdhocQuery";
import { useAuth } from "../../stores/auth";
import type * as ApiClient from "../../api/client";

// EventSource mock per the JobLogViewer pattern: a class capturing `url` +
// listeners with a manual emit() and a close() spy. emit() without data
// simulates a transport-style error (no SSE payload) — pre-stream 4xx JSON
// responses arrive that way because EventSource never exposes the body.
type Listener = (e: { data?: string }) => void;
class MockEventSource {
  static instances: MockEventSource[] = [];
  url: string;
  close = vi.fn();
  private listeners = new Map<string, Listener[]>();
  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }
  addEventListener(type: string, listener: Listener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
  removeEventListener() {}
  emit(type: string, data?: string) {
    for (const l of this.listeners.get(type) ?? []) l({ data });
  }
}

// Save-to-set transport capture: the questions POST records URL + parsed
// body; the sets catalog serves the one set the save dialog lists.
let postedTo = "";
let postedBody: unknown = null;
const apiMock = vi.fn(async (path: string, init?: RequestInit) => {
  if (init?.method === "POST") {
    postedTo = path;
    postedBody = JSON.parse(init.body as string);
    return new Response(JSON.stringify({}), { status: 201 });
  }
  if (path === "/api/projects/p1/question-sets") {
    return new Response(JSON.stringify({
      sets: [{ id: "s1", name: "客服常問 20 題", created_at: "2026-09-01T00:00:00Z" }],
    }), { status: 200 });
  }
  return new Response(JSON.stringify({}), { status: 200 });
});
// Real bodyOf/detailOf stay under test; only the transport is mocked.
vi.mock("../../api/client", async (importOriginal) => ({
  ...(await importOriginal()) as typeof ApiClient,
  api: (...args: unknown[]) => apiMock(...args as [string, RequestInit?]),
}));

const TIMINGS = { frames_ms: 1, search_ms: 2, citations_ms: 3, total_ms: 6 };

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
  useAuth.setState({ accessToken: "test-token" });
});

afterEach(() => {
  cleanup();
  // Keep .ant-message alive: antd's static message holder reuses that node;
  // removing it detaches the holder and later message.error() renders nowhere.
  vi.unstubAllGlobals();
  document.querySelectorAll(".ant-modal-root").forEach((el) => el.remove());
});

function mount(canUse = true) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AdhocQuery projectId="p1" canUse={canUse} />
    </QueryClientProvider>,
  );
}

async function startStream() {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  return MockEventSource.instances[0]!;
}

test("執行 opens EventSource with method, encoded query, response_type and token", async () => {
  mount();
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  const es = MockEventSource.instances[0]!;
  expect(es.url).toContain("/api/projects/p1/query/stream");
  expect(es.url).toContain("method=local");
  expect(es.url).toContain(`query=${encodeURIComponent("什麼是 GraphRAG?")}`);
  expect(es.url).toContain("response_type=multiple%20paragraphs");
  expect(es.url).toContain("token=test-token");
});

test("chunks append progressively into the answer area", async () => {
  mount();
  const es = await startStream();
  es.emit("chunk", JSON.stringify("第一段。"));
  expect(await screen.findByText("第一段。")).toBeInTheDocument();
  es.emit("chunk", JSON.stringify("第二段。"));
  expect(await screen.findByText("第一段。第二段。")).toBeInTheDocument();
});

test("citations render inside the 引用 collapse with label, ids and entry text (null → —)", async () => {
  mount();
  const user = userEvent.setup();
  const es = await startStream();
  es.emit("citations", JSON.stringify([
    { label: "Sources", ids: [2, 7], entries: [{ id: 2, text: "引用文字 A" }, { id: 7, text: null }] },
  ]));
  await user.click(await screen.findByText("引用 (1)"));
  expect(await screen.findByText("Sources #2, 7")).toBeInTheDocument();
  expect(screen.getByText("引用文字 A")).toBeInTheDocument();
  expect(screen.getByText("—")).toBeInTheDocument();
});

test("done renders the timings line rounded to whole ms and closes the EventSource", async () => {
  mount();
  const es = await startStream();
  // Fractional ms from the backend must render rounded (Math.round), not raw.
  es.emit("done", JSON.stringify({ frames_ms: 10.4, search_ms: 20548.6, citations_ms: 5.6, total_ms: 20564.9 }));
  expect(
    await screen.findByText("frames 10ms · 搜尋 20549ms · 引用 6ms · 總計 20565ms"),
  ).toBeInTheDocument();
  expect(es.close).toHaveBeenCalled();
  // Button re-enables once the stream finished.
  expect(screen.getByRole("button", { name: /^執\s?行$/ })).toBeEnabled();
});

test("執行 is disabled while streaming and until done", async () => {
  mount();
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  // No done event yet → mid-stream: the button must be disabled.
  expect(screen.getByRole("button", { name: /^執\s?行$/ })).toBeDisabled();
});

test("Shift+Enter inserts a newline without starting a query; Enter starts it", async () => {
  mount();
  const user = userEvent.setup();
  const box = screen.getByRole("textbox") as HTMLTextAreaElement;
  await user.type(box, "第一行");
  await user.type(box, "{Shift>}{Enter}{/Shift}");
  expect(MockEventSource.instances).toHaveLength(0);
  expect(box.value).toContain("\n");
  await user.type(box, "第二行{Enter}");
  expect(MockEventSource.instances).toHaveLength(1);
});

test("SSE error event surfaces the backend detail via message.error and closes", async () => {
  mount();
  const es = await startStream();
  es.emit("error", JSON.stringify({ detail: "查詢中斷" }));
  expect(await screen.findByText("查詢中斷")).toBeInTheDocument();
  expect(es.close).toHaveBeenCalled();
});

test("transport error (pre-stream 4xx / network) shows the generic message and closes", async () => {
  mount();
  const es = await startStream();
  // No payload = connection-level error, not an SSE error frame.
  es.emit("error");
  expect(await screen.findByText("查詢失敗,請稍後再試")).toBeInTheDocument();
  expect(es.close).toHaveBeenCalled();
});

test("unmount closes the EventSource", async () => {
  const { unmount } = render(
    <QueryClientProvider client={new QueryClient()}>
      <AdhocQuery projectId="p1" canUse />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "q");
  await user.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  const es = MockEventSource.instances[0]!;
  unmount();
  expect(es.close).toHaveBeenCalled();
});

test("canUse=false disables 執行", () => {
  mount(false);
  expect(screen.getByRole("button", { name: /^執\s?行$/ })).toBeDisabled();
});

test("proxy mode: EventSource URL carries no empty token param", async () => {
  useAuth.setState({ authMode: "proxy", accessToken: null });
  mount();
  const es = await startStream();
  expect(es.url).not.toContain("token=");
});

test("local mode: token still included", async () => {
  useAuth.setState({ authMode: "local", accessToken: "test-token" });
  mount();
  const es = await startStream();
  expect(es.url).toContain("token=test-token");
});

test("ad-hoc query still streams token by token", async () => {
  mount();
  await userEvent.type(screen.getByPlaceholderText(/輸入問題/), "hello");
  await userEvent.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  const es = MockEventSource.instances[0]!;
  es.emit("chunk", JSON.stringify("part one "));
  expect(await screen.findByText(/part one/)).toBeInTheDocument();
  es.emit("chunk", JSON.stringify("part two"));
  expect(await screen.findByText("part one part two")).toBeInTheDocument();
});

test("an ad-hoc answer saves into a question set in one action", async () => {
  mount();
  await userEvent.type(screen.getByPlaceholderText(/輸入問題/), "退貨要幾天?");
  await userEvent.click(screen.getByRole("button", { name: /^執\s?行$/ }));
  const es = MockEventSource.instances[0]!;
  es.emit("chunk", JSON.stringify("three working days"));
  es.emit("done", JSON.stringify(TIMINGS));

  await userEvent.click(await screen.findByRole("button", { name: "存成題目" }));
  // Pick the target set inside the save dialog (combobox opens the list).
  const dialog = await screen.findByRole("dialog");
  await userEvent.click(within(dialog).getByRole("combobox"));
  // antd v6: role="option" nodes are a non-interactive a11y mirror; the
  // clickable item is .ant-select-item-option-content (house pattern,
  // ExplorePanel/GraphView tests).
  await userEvent.click(
    await screen.findByText("客服常問 20 題", { selector: ".ant-select-item-option-content" }),
  );
  await userEvent.click(screen.getByRole("button", { name: /^確\s?定$/ }));

  await waitFor(() => {
    expect(postedTo).toBe("/api/projects/p1/question-sets/s1/questions");
    expect(postedBody).toEqual({ text: "退貨要幾天?" });
  });
});
