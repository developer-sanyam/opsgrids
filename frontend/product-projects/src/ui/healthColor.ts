import type { ProjectHealth } from "@/api/types";

/** The CSS colour token for each delivery health — for bars, timeline fills, donuts and legends. */
const HEALTH_COLOR: Record<ProjectHealth, string> = {
  on_track: "var(--color-present)",
  at_risk: "var(--color-late)",
  delayed: "var(--color-danger)",
};

export function healthColor(health: ProjectHealth): string {
  return HEALTH_COLOR[health];
}
