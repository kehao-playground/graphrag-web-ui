import { test, expect } from "vitest";
import { communityColor } from "../palette";

test("null community maps to the neutral gray", () => {
  expect(communityColor(null)).toBe("#d9d9d9");
});

test("same community yields the same color; ids wrap around the palette", () => {
  expect(communityColor(7)).toBe(communityColor(7));
  expect(communityColor(12)).toBe(communityColor(0));
  // negative ids take the absolute value (Math.abs) rather than crashing
  expect(communityColor(-1)).toBe(communityColor(1));
});

test("communities 0..11 are pairwise distinct", () => {
  const colors = Array.from({ length: 12 }, (_, c) => communityColor(c));
  expect(new Set(colors).size).toBe(12);
});
