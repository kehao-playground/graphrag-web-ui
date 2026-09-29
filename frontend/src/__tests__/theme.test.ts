import { expect, it } from "vitest";
import { theme } from "../theme";

// WCAG 2 contrast of an rgba() text colour alpha-blended over an opaque
// background (R4-21 measured antd's 0.45 default at 3.3 : 1).
function contrast(rgba: string, bg: [number, number, number]): number {
  const [r, g, b, a] = rgba.match(/[\d.]+/g)!.map(Number);
  const blended = [r, g, b].map((c, i) => c * a + bg[i] * (1 - a));
  const lum = (rgb: number[]) => {
    const [R, G, B] = rgb.map((c) => {
      const s = c / 255;
      return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * R + 0.7152 * G + 0.0722 * B;
  };
  const [l1, l2] = [lum(blended), lum(bg)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
}

it.each(["colorTextDescription", "colorTextTertiary"] as const)(
  "%s meets WCAG AA on the page background and on cards",
  (token) => {
    const value = theme.token?.[token];
    expect(typeof value).toBe("string");
    expect(contrast(value as string, [245, 245, 245])).toBeGreaterThanOrEqual(4.5); // layout #f5f5f5
    expect(contrast(value as string, [255, 255, 255])).toBeGreaterThanOrEqual(4.5); // cards
  },
);
