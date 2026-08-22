import { render, screen, cleanup } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, vi } from "vitest";
import QueryPanel from "../QueryPanel";
import { useAuth } from "../../stores/auth";

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
});

function mount(canUse = true) {
  render(<QueryPanel projectId="p1" canUse={canUse} />);
}

async function startStream() {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: "執行查詢" }));
  return MockEventSource.instances[0]!;
}

test("執行查詢 opens EventSource with method, encoded query, response_type and token", async () => {
  mount();
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: "執行查詢" }));
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
  expect(screen.getByRole("button", { name: "執行查詢" })).toBeEnabled();
});

test("執行查詢 is disabled while streaming and until done", async () => {
  mount();
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "什麼是 GraphRAG?");
  await user.click(screen.getByRole("button", { name: "執行查詢" }));
  // No done event yet → mid-stream: the button must be disabled.
  expect(screen.getByRole("button", { name: "執行查詢" })).toBeDisabled();
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
  const { unmount } = render(<QueryPanel projectId="p1" canUse />);
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox"), "q");
  await user.click(screen.getByRole("button", { name: "執行查詢" }));
  const es = MockEventSource.instances[0]!;
  unmount();
  expect(es.close).toHaveBeenCalled();
});

test("canUse=false disables 執行查詢", () => {
  mount(false);
  expect(screen.getByRole("button", { name: "執行查詢" })).toBeDisabled();
});
