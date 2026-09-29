import { describe, expect, it } from "vitest";
import enUS from "../locales/en-US";
import zhTW from "../locales/zh-TW";
import { resolveDetectedLanguage } from "../index";

type Tree = Record<string, unknown>;

const entries = (o: Tree, prefix = ""): [string, string][] =>
  Object.entries(o).flatMap(([k, v]): [string, string][] =>
    typeof v === "string"
      ? [[`${prefix}${k}`, v]]
      : entries(v as Tree, `${prefix}${k}.`));

const PLURAL = /_(one|other)$/;
// en-US carries `_one` beside every zh-TW `_other` (zh has one plural
// form); the trees are otherwise identical.
const keyTree = (o: Tree) =>
  [...new Set(entries(o).map(([k]) => k.replace(PLURAL, "_other")))].sort();

it("locales expose identical key trees (compile-time satisfies is primary; this is the backstop)", () => {
  expect(keyTree(enUS)).toEqual(keyTree(zhTW));
});

it.each([
  ["zh-TW", "zh-TW"], ["zh", "zh-TW"], ["zh-CN", "zh-TW"],
  ["zh-HK", "zh-TW"], ["zh-Hant", "zh-TW"],
  ["en-US", "en-US"], ["en", "en-US"], ["en-GB", "en-US"],
  ["fr-FR", "en-US"], ["ja-JP", "en-US"],
])("resolveDetectedLanguage(%s) → %s", (input, expected) => {
  expect(resolveDetectedLanguage(input)).toBe(expected);
});

// Catalog lints (F19). Each returns the offending keys so a failure names
// what to fix; the glossary they enforce heads each catalog file.
describe("zh-TW catalog", () => {
  it("uses full-width punctuation (a colon may only join an identifier)", () => {
    const bad = entries(zhTW).filter(([, v]) =>
      /[,;!?()]/.test(v) || /(?<![A-Za-z0-9_]):|:(?![A-Za-z0-9_])/.test(v));
    expect(bad.map(([k, v]) => `${k} = ${v}`)).toEqual([]);
  });

  it("names a job 任務 and keeps English nouns out of sentences", () => {
    const bad = entries(zhTW).filter(([, v]) =>
      v.includes("作業")
      || /\b(token|workspace|key|value|slug|frames|masked)\b/i.test(v.replace(/\{\{\w+\}\}/g, "")));
    expect(bad.map(([k, v]) => `${k} = ${v}`)).toEqual([]);
  });
});

describe("en-US catalog", () => {
  // Fragments spliced into a longer sentence, and values that open with a
  // lowercase identifier the reader types or sees verbatim.
  const LOWERCASE_OK = new Set(["files.tooLargeReason", "files.limitHint"]);
  const IDENTIFIER_START = /^(graphrag|settings\.yaml|input\/)/;

  it("starts every message in sentence case", () => {
    const bad = entries(enUS).filter(([k, v]) =>
      !LOWERCASE_OK.has(k) && !IDENTIFIER_START.test(v) && /^[^A-Za-z{]*[a-z]/.test(v));
    expect(bad.map(([k, v]) => `${k} = ${v}`)).toEqual([]);
  });

  // A count followed by a word needs both plural forms; these counts are
  // bare numbers in a label or never singular.
  const NO_PLURAL = new Set([
    "projects.healthPending", "overview.latestRunLine", "explore.truncatedWarning",
  ]);

  it("gives every counted noun a _one and an _other form", () => {
    const all = entries(enUS);
    const keys = new Set(all.map(([k]) => k));
    const bad = all.filter(([k, v]) =>
      /\{\{n\}\}/.test(v)
      || (/\{\{count\}\} [A-Za-z]/.test(v) && !PLURAL.test(k) && !NO_PLURAL.has(k))
      || (k.endsWith("_one") && !keys.has(k.replace(/_one$/, "_other"))));
    expect(bad.map(([k, v]) => `${k} = ${v}`)).toEqual([]);
  });
});

describe("catalog keys are used", () => {
  const sources = import.meta.glob<string>(
    ["../../**/*.{ts,tsx}", "!../../**/__tests__/**", "!../locales/**", "!../../**/*.test.{ts,tsx}"],
    { query: "?raw", import: "default", eager: true },
  );
  const code = Object.values(sources).join("\n");
  // Sections read through a template key (`errors.${code}`, …): their
  // members are the backend's or the parquet's vocabulary, checked by the
  // backend error-code test and the explore/labels closed sets instead.
  const DYNAMIC = [
    "errors.", "roles.", "roleDescriptions.", "auditActions.", "perms.",
    "explore.columns.", "files.state.", "files.ingestCheck.",
  ];

  it("every leaf key appears in the source", () => {
    const unused = entries(zhTW)
      .map(([k]) => k.replace(PLURAL, ""))
      .filter((k) => !DYNAMIC.some((p) => k.startsWith(p)) && !code.includes(`"${k}"`));
    expect(unused).toEqual([]);
  });
});
