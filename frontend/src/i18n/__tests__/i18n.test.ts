import { expect, it } from "vitest";
import enUS from "../locales/en-US";
import zhTW from "../locales/zh-TW";
import { resolveDetectedLanguage } from "../index";

const keyTree = (o: Record<string, unknown>): string[] =>
  Object.entries(o).flatMap(([k, v]) =>
    typeof v === "string"
      ? [k]
      : keyTree(v as Record<string, unknown>).map((c) => `${k}.${c}`));

it("locales expose identical key trees (compile-time satisfies is primary; this is the backstop)", () => {
  expect([...keyTree(enUS)].sort()).toEqual([...keyTree(zhTW)].sort());
});

it.each([
  ["zh-TW", "zh-TW"], ["zh", "zh-TW"], ["zh-CN", "zh-TW"],
  ["zh-HK", "zh-TW"], ["zh-Hant", "zh-TW"],
  ["en-US", "en-US"], ["en", "en-US"], ["en-GB", "en-US"],
  ["fr-FR", "en-US"], ["ja-JP", "en-US"],
])("resolveDetectedLanguage(%s) → %s", (input, expected) => {
  expect(resolveDetectedLanguage(input)).toBe(expected);
});
