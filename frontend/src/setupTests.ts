import { afterAll, afterEach } from "vitest";
import "@testing-library/jest-dom";

// jsdom lacks matchMedia; AntD's responsive observer needs it
if (typeof window.matchMedia !== "function") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

// jsdom also lacks ResizeObserver, which AntD v6 Table/Tabs measurement needs
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = ResizeObserverStub as unknown as typeof ResizeObserver;
}

// React 19's dev scheduler parks pending render work on setImmediate
// (performWorkUntilDeadline). jsdom environments are torn down per test file
// while those Node timers survive; a leftover callback then fires after the
// environment is gone and vitest reports an unhandled "ReferenceError: window
// is not defined" despite all tests passing (nondeterministic on CI, first
// seen after the lazy GraphView chunk landed). Drain the macrotask queue at
// the end of EVERY file — concurrent work can re-schedule itself, so loop a
// few turns instead of yielding once. The residual race this drain cannot
// close is filtered by signature in vite.config.ts (onUnhandledError).
afterAll(async () => {
  for (let i = 0; i < 10; i += 1) {
    const { promise, resolve } = Promise.withResolvers<void>();
    setTimeout(resolve, 0);
    await promise;
  }
});

import { i18n } from "./i18n";

// jsdom navigator.language is en-US; pin zh-TW so existing assertions
// keep holding (spec §6). afterEach kills within-file leakage from
// switcher tests (localStorage resets per file anyway).
await i18n.changeLanguage("zh-TW");
afterEach(async () => {
  await i18n.changeLanguage("zh-TW");
});
