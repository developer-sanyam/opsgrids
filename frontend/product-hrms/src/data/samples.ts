/**
 * The one representative (non-live) series this app still renders: the headcount-trend history.
 * `HeadcountTrend` labels it with the shared `SampleChip` so it is never mistaken for the
 * customer's own data. Recent Activity, Tasks Today and the sample attendance KPIs the audit
 * flagged (gap 9) are gone — real `GET /attendance` now backs the dashboard's attendance figures,
 * and nothing here fabricates an activity feed or a task list with no backend.
 */
import type { TrendPoint, TrendRange } from "@/api/types";

/** Headcount over time — a representative growth curve per range. */
export const HEADCOUNT_TREND: Record<TrendRange, TrendPoint[]> = {
  monthly: [
    { label: "Jan", value: 18 },
    { label: "Feb", value: 19 },
    { label: "Mar", value: 19 },
    { label: "Apr", value: 20 },
    { label: "May", value: 21 },
    { label: "Jun", value: 21 },
    { label: "Jul", value: 22 },
    { label: "Aug", value: 24 },
    { label: "Sep", value: 24 },
    { label: "Oct", value: 25 },
    { label: "Nov", value: 26 },
    { label: "Dec", value: 27 },
  ],
  quarterly: [
    { label: "Q1", value: 19 },
    { label: "Q2", value: 21 },
    { label: "Q3", value: 24 },
    { label: "Q4", value: 27 },
  ],
  yearly: [
    { label: "2022", value: 11 },
    { label: "2023", value: 16 },
    { label: "2024", value: 20 },
    { label: "2025", value: 23 },
    { label: "2026", value: 27 },
  ],
};

/** Percent change to show next to the trend headline, per range (representative). */
export const TREND_DELTA: Record<TrendRange, number> = {
  monthly: 5.2,
  quarterly: 9.1,
  yearly: 17.4,
};
