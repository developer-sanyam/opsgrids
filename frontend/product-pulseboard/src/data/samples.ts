/**
 * Representative history for surfaces that aggregate over time — the utilization-over-time chart
 * and the KPI deltas. The live totals (active projects, tasks due, this week's utilization) are
 * derived from the fixtures; these series give them a believable trend and prior-period delta.
 */
import type { TrendPoint } from "@/api/types";

/** Team utilization %, one point per week for the trailing twelve weeks. */
export const UTILIZATION_TREND: TrendPoint[] = [
  { label: "W23", value: 71 },
  { label: "W24", value: 69 },
  { label: "W25", value: 74 },
  { label: "W26", value: 76 },
  { label: "W27", value: 73 },
  { label: "W28", value: 78 },
  { label: "W29", value: 80 },
  { label: "W30", value: 77 },
  { label: "W31", value: 81 },
  { label: "W32", value: 79 },
  { label: "W33", value: 83 },
  { label: "W34", value: 82 },
];

/** Billable hours booked so far this month, with the change on the prior month. */
export const BILLABLE_THIS_MONTH = {
  hours: 1284,
  deltaPct: 6.4,
  series: [980, 1040, 1120, 1090, 1180, 1220, 1284],
};

/** Prior-period deltas for the Overview KPI cards, and small trailing series for the sparklines. */
export const KPI_TREND = {
  activeProjects: { deltaPct: 4.5, series: [9, 10, 10, 11, 11, 12, 12] },
  tasksDue: { deltaPct: -8.1, series: [14, 12, 13, 11, 10, 9, 9] },
  utilization: { deltaPct: 3.2, series: [74, 76, 73, 78, 80, 79, 82] },
};
