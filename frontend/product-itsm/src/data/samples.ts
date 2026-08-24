/**
 * Representative (non-live) series for the Overview's incident-volume chart. The live incident
 * list is a single day's snapshot, so the trend history over weeks / months / quarters is sample
 * data — every widget that reads it renders the shared `SampleChip` so a sample number is never
 * mistaken for a live one.
 */
import type { TrendPoint, TrendRange } from "@/api/types";

/** Opened vs resolved incidents over time — a representative operational curve per range. */
export const INCIDENT_VOLUME: Record<TrendRange, TrendPoint[]> = {
  weekly: [
    { label: "Mon", opened: 14, resolved: 11 },
    { label: "Tue", opened: 18, resolved: 16 },
    { label: "Wed", opened: 12, resolved: 15 },
    { label: "Thu", opened: 21, resolved: 17 },
    { label: "Fri", opened: 16, resolved: 19 },
    { label: "Sat", opened: 6, resolved: 8 },
    { label: "Sun", opened: 4, resolved: 5 },
  ],
  monthly: [
    { label: "Wk 1", opened: 72, resolved: 68 },
    { label: "Wk 2", opened: 81, resolved: 79 },
    { label: "Wk 3", opened: 64, resolved: 70 },
    { label: "Wk 4", opened: 77, resolved: 74 },
  ],
  quarterly: [
    { label: "Apr", opened: 268, resolved: 251 },
    { label: "May", opened: 312, resolved: 305 },
    { label: "Jun", opened: 289, resolved: 297 },
    { label: "Jul", opened: 301, resolved: 294 },
    { label: "Aug", opened: 276, resolved: 283 },
  ],
};

/** Percent change on opened volume vs the previous period, per range (representative). */
export const VOLUME_DELTA: Record<TrendRange, number> = {
  weekly: -6.4,
  monthly: 3.1,
  quarterly: -2.7,
};

/** Representative KPI deltas + sparkline series for the four Overview cards. */
export const KPI_SAMPLE = {
  openIncidents: { delta: -8.3, series: [22, 25, 24, 27, 23, 21, 19] },
  slaCompliance: { delta: 1.6, series: [88, 90, 89, 91, 92, 91, 93] },
  awaitingApproval: { delta: 4.5, series: [3, 4, 5, 4, 6, 5, 4] },
  assetsManaged: { delta: 2.2, series: [148, 150, 151, 153, 155, 156, 158] },
};
