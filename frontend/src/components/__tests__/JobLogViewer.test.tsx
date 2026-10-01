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

// The viewer mints a ticket per open (F24-01): the stub numbers them, so a
// test can tell a reopen's fresh ticket from the first one.
const ticketBodies: unknown[] = [];
let tickets = 0;

async function opened(n: number) {
  await waitFor(() => expect(MockEventSource.instances.length).toBeGreaterThan(n));
  return MockEventSource.instances[n]!;
}

beforeEach(() => {
  MockEventSource.instances = [];
  ticketBodies.length = 0;
  tickets = 0;
  vi.stubGlobal("EventSource", MockEventSource);
  vi.stubGlobal("fetch", vi.fn(async (path: string, init?: RequestInit) => {
    expect(path).toBe("/api/auth/sse-ticket");
    ticketBodies.push(JSON.parse(init?.body as string));
    tickets += 1;
    return new Response(JSON.stringify({ ticket: `tkt-${tickets}`, expires_in: 60 }), { status: 200 });
  }));
  useAuth.setState({ accessToken: "test-token", authMode: "local" });
});

afterEach(() => vi.unstubAllGlobals());

test("appends log chunks and opens the stream with a ticket for its path (F24-01)", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
  expect(es.url).toBe("/api/jobs/j1/logs?ticket=tkt-1");
  expect(ticketBodies).toEqual([{ path: "/api/jobs/j1/logs" }]);
  es.emit("log", JSON.stringify("hello "));
  es.emit("log", JSON.stringify("world\n"));
  await waitFor(() => expect(logPre().textContent).toBe("hello world\n"));
});

test("chunks arriving in one frame are written to the DOM once (R1-85)", async () => {
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => frames.push(cb));
  vi.stubGlobal("cancelAnimationFrame", () => {});
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
  frames.length = 0; // the Drawer's own open animation
  for (const c of ["a", "b", "c"]) es.emit("log", JSON.stringify(c));
  expect(frames).toHaveLength(1); // one flush scheduled for the whole burst
  expect(logPre().textContent).toBe("");
  act(() => frames[0]!(0));
  expect(logPre().textContent).toBe("abc");
  expect(logPre().childNodes).toHaveLength(1);
});

test("done event closes the stream", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
  es.emit("done", JSON.stringify({ offset: 10, status: "succeeded" }));
  expect(es.close).toHaveBeenCalled();
});

test("unmount closes the EventSource", async () => {
  const { unmount } = render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
  unmount();
  expect(es.close).toHaveBeenCalled();
});

test("follows the tail until the reader scrolls up, and Follow resumes it (R3-19)", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
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
  const es = await opened(0);
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
  const es = await opened(0);
  es.fail(0); // CONNECTING: native reconnect with Last-Event-ID
  expect(screen.getByText(/正在重新連線/)).toBeInTheDocument();
  es.readyState = 1;
  es.emit("log", JSON.stringify("more"), "4");
  expect(screen.queryByText(/正在重新連線/)).not.toBeInTheDocument();
});

test("a refused reconnect says so and Reconnect resumes at the last offset with a fresh ticket (R2-32)", async () => {
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(0);
  es.emit("log", JSON.stringify("first part\n"), "11");
  await waitFor(() => expect(logPre().textContent).toBe("first part\n"));

  es.fail(2); // CLOSED: e.g. the ticket expired and the retry got a 401
  expect(screen.getByText(/日誌串流已中斷/)).toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "重新連線" }));
  const again = await opened(1);
  expect(es.close).toHaveBeenCalled();
  expect(again.url).toBe("/api/jobs/j1/logs?offset=11&ticket=tkt-2");
  expect(screen.queryByText(/日誌串流已中斷/)).not.toBeInTheDocument();

  again.emit("log", JSON.stringify("second part\n"), "23");
  await waitFor(() => expect(logPre().textContent).toBe("first part\nsecond part\n"));
});

test("a refused ticket shows the stream as lost", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(
    JSON.stringify({ detail: "password change required", code: "auth_must_change_password" }),
    { status: 403 },
  )));
  render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  expect(await screen.findByText(/日誌串流已中斷/)).toBeInTheDocument();
  expect(MockEventSource.instances).toHaveLength(0);
});

test("reopening the drawer replays the log from the start", async () => {
  const { rerender } = render(<JobLogViewer jobId="j1" onClose={() => {}} />);
  (await opened(0)).emit("log", JSON.stringify("old\n"), "4");
  await waitFor(() => expect(logPre().textContent).toBe("old\n"));
  rerender(<JobLogViewer jobId={null} onClose={() => {}} />);
  rerender(<JobLogViewer jobId="j1" onClose={() => {}} />);
  const es = await opened(1);
  expect(es.url).toBe("/api/jobs/j1/logs?ticket=tkt-2");
  expect(logPre().textContent).toBe("");
});
