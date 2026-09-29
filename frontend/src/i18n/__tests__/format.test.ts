import { expect, it } from "vitest";
import { formatDateTime, formatShortDateTime, parseDateTime } from "../format";

const ISO = "2026-09-21T00:21:34.958879+00:00";

it("formatDateTime renders through the given locale, never the raw ISO (R1-54)", () => {
  const zh = formatDateTime(ISO, "zh-TW");
  const en = formatDateTime(ISO, "en-US");
  expect(zh).not.toContain("T00:21");
  expect(zh).toContain("2026");
  expect(en).toContain("2026");
  expect(en).toMatch(/Sep/);
  expect(zh).not.toEqual(en);
});

it("formatShortDateTime carries month, day and time but no year (matrix headers, R4-33)", () => {
  const en = formatShortDateTime(ISO, "en-US");
  expect(en).toMatch(/Sep/);
  expect(en).not.toContain("2026");
  expect(en).toMatch(/\d{1,2}:\d{2}/);
});

it("parses graphrag's '+0000' creation_date form", () => {
  const d = parseDateTime("2026-09-21 00:18:35 +0000");
  expect(d?.toISOString()).toBe("2026-09-21T00:18:35.000Z");
  expect(formatDateTime("2026-09-21 00:18:35 +0000", "en-US")).toMatch(/Sep/);
});

it("an unparseable value comes back unchanged", () => {
  expect(parseDateTime("not a date")).toBeNull();
  expect(formatDateTime("not a date", "en-US")).toBe("not a date");
});
