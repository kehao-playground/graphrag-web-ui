import type { ThemeConfig } from "antd";

// antd's secondary and tertiary text (rgba 0,0,0 at 0.45) measures 3.3 : 1
// on the page background, below WCAG AA's 4.5 : 1, and it carries stat
// labels, hints, timings and the identity line (R4-21). 0.65 is 5.7 : 1;
// disabled text keeps its own token (colorTextDisabled) untouched.
export const theme: ThemeConfig = {
  token: {
    colorTextDescription: "rgba(0, 0, 0, 0.65)",
    colorTextTertiary: "rgba(0, 0, 0, 0.65)",
  },
};
