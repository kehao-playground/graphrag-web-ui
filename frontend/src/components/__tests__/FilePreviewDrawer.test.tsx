import { render, screen, waitFor } from "@testing-library/react";
import { vi } from "vitest";
import { QueryClientProvider } from "@tanstack/react-query";
import { createQueryClient } from "../../api/queryClient";
import FilePreviewDrawer, { type Locator } from "../files/FilePreviewDrawer";
import { stubFetch } from "../../testing/stubFetch";

let body = { text: "", offset: 0, total_size: 0, match: false };
stubFetch(vi.fn(async () => new Response(JSON.stringify(body), { status: 200 })));

function open(locator?: Locator, highlight?: string) {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <FilePreviewDrawer projectId="p1" name="a.txt" locator={locator} highlight={highlight} onClose={() => {}} />
    </QueryClientProvider>,
  );
}

test("a matched citation marks the passage, scrolls to it and says so in plain words", async () => {
  const scroll = vi.fn();
  Element.prototype.scrollIntoView = scroll;
  body = { text: "before the cited passage after", offset: 0, total_size: 1543, match: true };
  open({ passage: "the cited passage" }, "the cited passage");
  const mark = await screen.findByText("the cited passage");
  expect(mark.tagName).toBe("MARK");
  expect(screen.getByText("顯示引用段落 · 文件大小 1.5 KiB")).toBeInTheDocument();
  await waitFor(() => expect(scroll).toHaveBeenCalled());
  expect(screen.queryByText(/命中|視窗起點/)).not.toBeInTheDocument();
});

test("an unmatched citation says the passage is gone", async () => {
  body = { text: "head of file", offset: 0, total_size: 12, match: false };
  open({ passage: "missing" }, "missing");
  expect(await screen.findByText(/已找不到引用段落/)).toBeInTheDocument();
  expect(document.querySelector("mark")).toBeNull();
});

test("the head window has no header, and words wrap without splitting", async () => {
  body = { text: "Kings Applications", offset: 0, total_size: 18, match: false };
  open();
  const pre = (await screen.findByText("Kings Applications")).closest("pre")!;
  expect(pre.style.overflowWrap).toBe("anywhere");
  expect(pre.style.wordBreak).toBe("normal");
  expect(screen.queryByText(/引用段落/)).not.toBeInTheDocument();
});

// R4-41: the drawer says how to leave it from the keyboard.
test("the drawer header hints that Esc closes it", async () => {
  body = { text: "x", offset: 0, total_size: 1, match: false };
  open();
  expect(await screen.findByText("按 Esc 關閉")).toBeInTheDocument();
});
