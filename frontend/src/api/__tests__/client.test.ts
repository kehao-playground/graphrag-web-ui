import { expect, test } from "vitest";
import { bodyOf, detailOf } from "../client";

test("detailOf surfaces zh-TW detail verbatim", async () => {
  const r = new Response(JSON.stringify({ detail: "找不到該筆資料" }), { status: 404 });
  expect(await detailOf(r, "fallback")).toBe("找不到該筆資料");
});

test("detailOf falls back on non-JSON body", async () => {
  const r = new Response("<html>", { status: 502 });
  expect(await detailOf(r, "fallback")).toBe("fallback");
});

test("detailOf falls back when detail is absent or non-string", async () => {
  const missing = new Response(JSON.stringify({ other: 1 }), { status: 409 });
  expect(await detailOf(missing, "fallback")).toBe("fallback");
  const object = new Response(JSON.stringify({ detail: { nested: true } }), { status: 400 });
  expect(await detailOf(object, "fallback")).toBe("fallback");
});

test("bodyOf returns parsed body, empty object on non-JSON", async () => {
  const json = new Response(JSON.stringify({ current_hash: "h1" }), { status: 409 });
  expect(await bodyOf(json)).toEqual({ current_hash: "h1" });
  const html = new Response("<html>", { status: 502 });
  expect(await bodyOf(html)).toEqual({});
});
