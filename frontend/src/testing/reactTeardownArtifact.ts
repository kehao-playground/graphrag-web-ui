/**
 * Recognizes the one unhandled error vitest must not fail the run on.
 *
 * React 19.2's dev scheduler reads `window.event` unconditionally when
 * flushing work from a setImmediate task. Work still queued when vitest
 * tears down the jsdom environment fires with no `window`, which fails the
 * whole run even though every test passed — an upstream race the
 * setupTests afterAll drain mitigates but cannot fully close (see
 * jaegertracing/jaeger-ui#4339). vitest blames whichever file happened to
 * be running, so the file name carries no signal either.
 *
 * Matching is on the stack's *shape*, not on one frame's name: the frame
 * that surfaces varies between runs. The first version of this filter
 * required `performWorkOnRootViaSchedulerTask` and broke the moment the
 * same race surfaced with an anonymous top frame:
 *
 *     ❯ node_modules/react-dom/cjs/react-dom-client.development.js:17920:15
 *     ❯ Immediate.performWorkUntilDeadline node_modules/scheduler/...:45:48
 *     ❯ processImmediate node:internal/timers:534:21
 *
 * The property that actually identifies the artifact is that the stack
 * lives *entirely* inside React's own scheduling machinery. A real
 * ReferenceError from our code always has at least one frame in src/, so
 * it still fails the run — which is the whole point of the filter being
 * narrow.
 */
export function isReactSchedulerTeardownArtifact(error: unknown): boolean {
  // Errors cross the worker/fork boundary as plain serialized objects, so
  // match structurally rather than with instanceof.
  const { message, stack } = (error ?? {}) as { message?: string; stack?: string };
  if (message !== "window is not defined") return false;
  if (!stack) return false;

  const frames = stack.split("\n");
  const fromReactInternals = frames.some((line) =>
    /node_modules[/\\](react-dom|scheduler)[/\\]/.test(line),
  );
  if (!fromReactInternals) return false;

  // A frame in our own sources means real app code ran — not the teardown
  // race. `node_modules` is excluded because dependencies ship src/ dirs too.
  const touchesAppCode = frames.some(
    (line) => /[/\\]src[/\\]/.test(line) && !line.includes("node_modules"),
  );
  return !touchesAppCode;
}
