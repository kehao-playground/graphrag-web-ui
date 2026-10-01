import { lazy } from "react";
import type { ComponentType } from "react";

// A route page in its own chunk (R1-60). Unlike a bare React.lazy, a failed
// import is not cached for good: the failure swaps in a fresh lazy, so the
// surrounding ErrorBoundary's Retry (or a navigation, which resets it)
// fetches the chunk again instead of re-throwing the same rejection.
export function lazyPage<P extends object>(load: () => Promise<{ default: ComponentType<P> }>) {
  const fresh = () => lazy(() => load().catch((e: unknown) => {
    current = fresh();
    throw e;
  }));
  let current = fresh();
  return function LazyPage(props: P) {
    const Current = current;
    return <Current {...props} />;
  };
}
