import { test, expect } from "vitest";
import { sentenceDiff } from "../tests/sentenceDiff";

// Task 8, spec §9.2: diff granularity is sentences, not characters —
// GraphRAG answers are prose and a character diff buries the real change
// in noise. Splitting and comparison are pure, with no DOM and no React.

test("splits on sentence boundaries, not on characters", () => {
  const out = sentenceDiff("A one. B two. C three.", "A one. B changed. C three.");
  expect(out.filter((s) => s.side === "both").map((s) => s.text.trim()))
    .toEqual(["A one.", "C three."]);
  expect(out.filter((s) => s.side === "left").map((s) => s.text.trim())).toEqual(["B two."]);
  expect(out.filter((s) => s.side === "right").map((s) => s.text.trim())).toEqual(["B changed."]);
});

test("identical answers produce no differing segments", () => {
  expect(sentenceDiff("Same. Text.", "Same. Text.").every((s) => s.side === "both")).toBe(true);
});

test("an empty side yields every sentence on the other", () => {
  expect(sentenceDiff("", "Only right.").map((s) => s.side)).toEqual(["right"]);
});

test("CJK full stops are sentence boundaries too", () => {
  const out = sentenceDiff("第一句。第二句。", "第一句。改過了。");
  expect(out.filter((s) => s.side === "both").map((s) => s.text)).toEqual(["第一句。"]);
});

test("inter-sentence whitespace survives the diff", () => {
  // English answers carry a space between sentences; the diff must not
  // jam them together when the segments render inline.
  const out = sentenceDiff("Same. Text.", "Same. Text.");
  expect(out.map((s) => s.text)).toEqual(["Same. Text."]);
  expect(sentenceDiff("A one. B two.", "A one. B changed.")[0].text).toBe("A one. ");
});
