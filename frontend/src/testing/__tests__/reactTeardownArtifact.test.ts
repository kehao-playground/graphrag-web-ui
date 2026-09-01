import { expect, test } from "vitest";
import { isReactSchedulerTeardownArtifact } from "../reactTeardownArtifact";

// Both stacks below are verbatim from real CI failures. The filter used to
// key on one frame name and let the second shape through, failing a run in
// which all 101 tests passed.

const NAMED_FRAME_STACK = [
  "ReferenceError: window is not defined",
  "    at performWorkOnRootViaSchedulerTask (node_modules/react-dom/cjs/react-dom-client.development.js:17920:15)",
  "    at performWorkUntilDeadline (node_modules/scheduler/cjs/scheduler.development.js:45:48)",
].join("\n");

const ANONYMOUS_FRAME_STACK = [
  "ReferenceError: window is not defined",
  "    at node_modules/react-dom/cjs/react-dom-client.development.js:17920:15",
  "    at Immediate.performWorkUntilDeadline node_modules/scheduler/cjs/scheduler.development.js:45:48",
  "    at processImmediate node:internal/timers:534:21",
].join("\n");

test("ignores the teardown race when the top frame is named", () => {
  expect(
    isReactSchedulerTeardownArtifact({
      message: "window is not defined",
      stack: NAMED_FRAME_STACK,
    }),
  ).toBe(true);
});

test("ignores the same race when the top frame is anonymous", () => {
  // The regression: this shape is what CI actually produced, and the
  // name-keyed filter failed the run on it.
  expect(
    isReactSchedulerTeardownArtifact({
      message: "window is not defined",
      stack: ANONYMOUS_FRAME_STACK,
    }),
  ).toBe(true);
});

test("still fails the run for the same message raised from app code", () => {
  // The filter must stay narrow: a real ReferenceError in our components
  // has a src/ frame and has to keep breaking the build.
  const stack = [
    "ReferenceError: window is not defined",
    "    at GraphView (/home/runner/work/repo/frontend/src/components/GraphView.tsx:42:3)",
    "    at renderWithHooks (node_modules/react-dom/cjs/react-dom-client.development.js:1:1)",
  ].join("\n");
  expect(isReactSchedulerTeardownArtifact({ message: "window is not defined", stack })).toBe(
    false,
  );
});

test("a dependency shipping its own src/ directory is not mistaken for app code", () => {
  const stack = [
    "ReferenceError: window is not defined",
    "    at node_modules/some-dep/src/index.js:1:1",
    "    at performWorkUntilDeadline (node_modules/scheduler/cjs/scheduler.development.js:45:48)",
  ].join("\n");
  expect(isReactSchedulerTeardownArtifact({ message: "window is not defined", stack })).toBe(true);
});

test("a different message is never ignored", () => {
  expect(
    isReactSchedulerTeardownArtifact({
      message: "document is not defined",
      stack: ANONYMOUS_FRAME_STACK,
    }),
  ).toBe(false);
});

test("the right message with an unrelated stack is never ignored", () => {
  const stack = ["ReferenceError: window is not defined", "    at Object.<anonymous> (server.js:1:1)"].join("\n");
  expect(isReactSchedulerTeardownArtifact({ message: "window is not defined", stack })).toBe(
    false,
  );
});

test("a stackless or absent error is never ignored", () => {
  expect(isReactSchedulerTeardownArtifact({ message: "window is not defined" })).toBe(false);
  expect(isReactSchedulerTeardownArtifact(undefined)).toBe(false);
  expect(isReactSchedulerTeardownArtifact(null)).toBe(false);
});
