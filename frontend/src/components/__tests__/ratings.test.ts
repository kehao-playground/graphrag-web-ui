import { RATING_META, RATING_ORDER, SCORES } from "../tests/ratings";

// One vocabulary (R1-115): the matrix tags, the drawer's Segmented and the
// 1/2/3 shortcuts all read SCORES/RATING_META, so the order the regression
// filter ranks by is the order the user sees and presses.
test("SCORES runs best to worst and RATING_ORDER is its index", () => {
  expect(SCORES).toEqual(["good", "fair", "poor"]);
  SCORES.forEach((score, i) => expect(RATING_ORDER[score]).toBe(i));
});

test("every score has a label key and a tag colour", () => {
  expect(Object.keys(RATING_META).sort()).toEqual([...SCORES].sort());
  expect(RATING_META.good).toEqual({ labelKey: "workbench.ratingGood", color: "green" });
  expect(RATING_META.fair).toEqual({ labelKey: "workbench.ratingFair", color: "gold" });
  expect(RATING_META.poor).toEqual({ labelKey: "workbench.ratingPoor", color: "red" });
});
