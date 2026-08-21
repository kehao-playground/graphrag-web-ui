import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, vi } from "vitest";
import JobLogViewer from "../JobLogViewer";
import { useAuth } from "../../stores/auth";

// EventSource mock per the Task 7 plan: a class capturing `url` + listeners
// with a manual emit() and a close() spy. A faithful mock would also stop
// delivering after close(), like the native implementation.
type Listener = (e: { data: string }) => void;
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
  emit(type: string, data: string) {
    for (const l of this.listeners.get(type) ?? []) l({ data });
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
  useAuth.setState({ accessToken: "test-token" });
});

afterEach(() => vi.unstubAllGlobals());

test("appends log chunks and passes the access token as a query param", async () => {
  render(<JobLogViewer jobId="j1" open onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  expect(es.url).toBe("/api/jobs/j1/logs?token=test-token");
  es.emit("log", JSON.stringify("hello "));
  es.emit("log", JSON.stringify("world\n"));
  expect(await screen.findByText(/hello world/)).toBeInTheDocument();
});

test("done event closes the stream", () => {
  render(<JobLogViewer jobId="j1" open onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  es.emit("done", JSON.stringify({ offset: 10, status: "succeeded" }));
  expect(es.close).toHaveBeenCalled();
});

test("unmount closes the EventSource", () => {
  const { unmount } = render(<JobLogViewer jobId="j1" open onClose={() => {}} />);
  const es = MockEventSource.instances[0]!;
  unmount();
  expect(es.close).toHaveBeenCalled();
});
