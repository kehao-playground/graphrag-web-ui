import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import JobLogViewer from "../JobLogViewer";
import { useAuth } from "../../stores/auth";

// EventSource mock per the Task 7 plan: a class capturing `url` + listeners
// with a manual emit() and a close() spy. readyState and lastEventId follow
// the native semantics the viewer reads: CLOSED after a refused reconnect.
type Listener = (e: { data: string; lastEventId: string }) => void;
class MockEventSource {
  static readonly CLOSED = 2;
  static instances: MockEventSource[] = [];
  url: string;
  readyState = 1;
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
  emit(type: string, data: string, lastEventId = "") {
    act(() => {
      for (const l of this.listeners.get(type) ?? []) l({ data, lastEventId });
    });
  }
  fail(readyState: 0 | 2) {
    this.readyState = readyState;
    this.emit("error", "");
  }
}

const logPre = () => document.querySelector("pre")!;

// jsdom has no layout: give the <pre> a fixed geometry so the follow logic
// can tell "at the bottom" from "scrolled up".
function layout(pre: HTMLElement, scrollHeight: number, clientHeight = 100) {
  Object.defineProperty(pre, "scrollHeight", { configurable: true, value: scrollHeight });
  Object.defineProperty(pre, "clientHeight", { configurable: true, value: clientHeight });
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
  useAuth.setState({ accessToken: "test-token", authMode: "local" });
});

afterEach(() => vi.unstubAllGlobals());

test("appends log chunks and passes the access token as a query param", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  expect(es.url).toBe("/api/jobs/j1/logs?token=test-token");
  es.emit("log", JSON.stringify("hello "));
  es.emit("log", JSON.stringify("world\n"));
  await waitFor(() => expect(logPre().textContent).toBe("hello world\n"));
});

test("chunks arriving in one frame are written to the DOM once (R1-85)", async () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => {});
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  frames.length = 0; // the Drawer's own open animation
  const es = MockEventSource.instances[0]!;
  for (const c of ["a", "b", "c"]) es.emit("log", JSON.stringify(c));
  expect(frames).toHaveLength(1); // one flush scheduled for the whole burst
  expect(logPre().textContent).toBe("");
  act(() => frames[0]!(0));
  expect(logPre().textContent).toBe("abc");
  expect(logPre().childNodes).toHaveLength(1);
});

test("done event closes the stream", () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  es.emit("done", JSON.stringify({ offset: 10, status: "succeeded" }));
  expect(es.close).toHaveBeenCalled();
});

test("unmount closes the EventSource", () => {
  const { unmount } = render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  unmount();
  expect(es.close).toHaveBeenCalled();
});

test("follows the tail until the reader scrolls up, and Follow resumes it (R3-19)", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  const pre = logPre();
  layout(pre, 500);
  es.emit("log", JSON.stringify("line 1\n"));
  await waitFor(() => expect(pre.scrollTop).toBe(500));

  // Reader scrolls up to read an earlier line: new output must not yank it.
  pre.scrollTop = 100;
  fireEvent.scroll(pre);
  layout(pre, 800);
  es.emit("log", JSON.stringify("line 2\n"));
  await waitFor(() => expect(pre.textContent).toContain("line 2"));
  expect(pre.scrollTop).toBe(100);

  fireEvent.click(screen.getByRole("button", { name: "跟隨最新" }));
  expect(pre.scrollTop).toBe(800);
  expect(screen.getByRole("button", { name: "暫停自動捲動" })).toBeInTheDocument();
});

test("Pause stops following without scrolling", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  const pre = logPre();
  layout(pre, 500);
  fireEvent.click(screen.getByRole("button", { name: "暫停自動捲動" }));
  es.emit("log", JSON.stringify("x\n"));
  await waitFor(() => expect(pre.textContent).toBe("x\n"));
  expect(pre.scrollTop).toBe(0);
  expect(screen.getByRole("button", { name: "跟隨最新" })).toBeInTheDocument();
});

test("a dropped connection the browser retries shows a notice until data flows again", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  es.fail(0); // CONNECTING: native reconnect with Last-Event-ID
  expect(screen.getByText(/正在重新連線/)).toBeInTheDocument();
  es.readyState = 1;
  es.emit("log", JSON.stringify("more"), "4");
  expect(screen.queryByText(/正在重新連線/)).not.toBeInTheDocument();
});

test("a refused reconnect says so and Reconnect resumes at the last offset with a fresh token (R2-32)", async () => {
  const refresh = vi.fn(async () => {
    useAuth.setState({ accessToken: "fresh-token" });
    return "fresh-token";
  });
  useAuth.setState({ refresh });
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  es.emit("log", JSON.stringify("first part\n"), "11");
  await waitFor(() => expect(logPre().textContent).toBe("first part\n"));

  es.fail(2); // CLOSED: e.g. the ?token= expired and the retry got a 401
  expect(screen.getByText(/日誌串流已中斷/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重新連線" }));
  await waitFor(() => expect(MockEventSource.instances).toHaveLength(2));
  expect(refresh).toHaveBeenCalled();
  expect(es.close).toHaveBeenCalled();
  const again = MockEventSource.instances[1]!;
  expect(again.url).toBe("/api/jobs/j1/logs?offset=11&token=fresh-token");
  expect(screen.queryByText(/日誌串流已中斷/)).not.toBeInTheDocument();

  again.emit("log", JSON.stringify("second part\n"), "23");
  await waitFor(() => expect(logPre().textContent).toBe("first part\nsecond part\n"));
});

test("reopening the drawer replays the log from the start", async () => {
  const { rerender } = render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  MockEventSource.instances[0]!.emit("log", JSON.stringify("old\n"), "4");
  await waitFor(() => expect(logPre().textContent).toBe("old\n"));
  rerender(<JobLogViewer jobId={null} onClose={() => {}} />);
  rerender(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = MockEventSource.instances[1]!;
  expect(es.url).toBe("/api/jobs/j1/logs?token=test-token");
  expect(logPre().textContent).toBe("");
});
