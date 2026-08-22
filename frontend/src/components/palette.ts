// 12-color categorical palette for community coloring, chosen to stay
// distinguishable side by side on the sigma canvas.
const PALETTE = [
  "#e6194b", "#f58231", "#bcf60c", "#3cb44b", "#42d4f4", "#4363d8",
  "#911eb4", "#f032e6", "#fabed4", "#46f0f0", "#9a6324", "#800000",
] as const;

// null community = entity outside any community at the chosen level → antd
// neutral gray so unassigned nodes read as background.
export function communityColor(community: number | null): string {
  if (community === null) return "#d9d9d9";
  return PALETTE[Math.abs(community) % PALETTE.length];
}
