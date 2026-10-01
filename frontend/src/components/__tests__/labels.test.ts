import { expect, test } from "vitest";
import { i18n } from "../../i18n";
import { jobMethodLabel, jobTypeLabel, jobTypeShortLabel, roleLabel } from "../labels";

const t = i18n.t.bind(i18n);

test("jobTypeLabel names every job type in sentence form, unknowns raw", () => {
  expect(jobTypeLabel("index", t)).toBe(t("workbench.typeIndex"));
  expect(jobTypeLabel("update", t)).toBe(t("workbench.typeUpdate"));
  expect(jobTypeLabel("test_run", t)).toBe(t("workbench.typeTestRun"));
  expect(jobTypeLabel("mystery", t)).toBe("mystery");
});

test("jobTypeShortLabel covers test_run too (R1-49)", () => {
  expect(jobTypeShortLabel("index", t)).toBe(t("jobs.typeIndex"));
  expect(jobTypeShortLabel("update", t)).toBe(t("jobs.typeUpdate"));
  expect(jobTypeShortLabel("test_run", t)).toBe(t("jobs.typeTestRun"));
  expect(jobTypeShortLabel("mystery", t)).toBe("mystery");
});

test("jobMethodLabel names the index methods, unknowns raw", () => {
  expect(jobMethodLabel("standard", t)).toBe(t("jobs.methodStandard"));
  expect(jobMethodLabel("fast", t)).toBe(t("jobs.methodFast"));
  expect(jobMethodLabel("local", t)).toBe("local");
});

test("roleLabel localizes built-in roles only", () => {
  expect(roleLabel({ name: "viewer", is_system: true }, t)).toBe(t("roles.viewer"));
  expect(roleLabel({ name: "viewer", is_system: false }, t)).toBe("viewer");
});
