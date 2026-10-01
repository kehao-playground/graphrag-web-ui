import { Suspense } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import ErrorBoundary from "../ErrorBoundary";
import { lazyPage } from "../lazyPage";

beforeEach(() => { vi.spyOn(console, "error").mockImplementation(() => {}); });
afterEach(() => { vi.restoreAllMocks(); });

function mount(Page: React.ComponentType) {
  render(
    <ErrorBoundary>
      <Suspense fallback={<p>loading</p>}>
        <Page />
      </Suspense>
    </ErrorBoundary>,
  );
}

test("a page chunk loads behind the Suspense fallback", async () => {
  const Page = lazyPage(async () => ({ default: () => <p>page body</p> }));
  mount(Page);
  expect(await screen.findByText("page body")).toBeInTheDocument();
});

// React.lazy caches a rejected import for good: without the swap, Retry
// would re-throw the same failure and only a full reload could recover.
test("a failed chunk is fetched again on the boundary's Retry", async () => {
  let online = false;
  const load = vi.fn(async () => {
    if (!online) throw new Error("Failed to fetch dynamically imported module");
    return { default: () => <p>page body</p> };
  });
  const Page = lazyPage(load);
  mount(Page);
  expect(await screen.findByText("此區塊載入失敗")).toBeInTheDocument();
  online = true;
  await userEvent.click(screen.getByRole("button", { name: /^重\s?試$/ }));
  expect(await screen.findByText("page body")).toBeInTheDocument();
});
