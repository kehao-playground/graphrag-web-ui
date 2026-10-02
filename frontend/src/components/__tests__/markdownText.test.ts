import { test, expect } from "vitest";
import { plainText } from "../tests/markdownText";

// V-09: the compare modal diffs the text the drawer renders — no heading,
// emphasis or code markers, and no [Data: …] citation markers.
test("plainText drops Markdown syntax and citation markers", () => {
  const md = [
    "### Grace Hopper",
    "",
    "She wrote the **first** compiler [Data: Sources (12); Entities (3, 4)]. It was *A-0*.",
    "",
    "- `COBOL` came later",
    "- so did FLOW-MATIC",
    "",
    "---",
  ].join("\n");
  expect(plainText(md)).toBe([
    "Grace Hopper",
    "",
    "She wrote the first compiler. It was A-0.",
    "",
    "COBOL came later",
    "so did FLOW-MATIC",
  ].join("\n"));
});
