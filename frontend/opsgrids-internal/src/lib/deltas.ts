/**
 * Month-over-month deltas for the Super Admin dashboard's KPIs, off `GET /admin/metrics/series`'s
 * `SeriesPointOut[]`. A delta is shown **only when both the latest and the previous month have a
 * real value** — `null` on either side means "no delta pill", never a fabricated `+0%` (the owner
 * brief's rule for this dashboard).
 */

import type { SeriesPointOut } from "@/api/types";

/** The percentage change from `previous` to `current`, or `null` when either is absent or the
 *  base is zero (a percentage change off zero is undefined, not "infinite growth"). */
export function pctDelta(previous: number | null, current: number | null): number | null {
  if (previous === null || current === null || previous === 0) return null;
  return ((current - previous) / previous) * 100;
}

/** The delta between the last two points of a monthly series — "this month vs last month". */
export function seriesDelta(points: SeriesPointOut[]): number | null {
  if (points.length < 2) return null;
  const current = points[points.length - 1];
  const previous = points[points.length - 2];
  if (!current || !previous) return null;
  return pctDelta(previous.value, current.value);
}

/**
 * The growth rate of a running total, given the count of what was *added* this month and the
 * total as it stands right now (a live count, not an end-of-month snapshot — the two are close
 * enough for a monthly-cadence dashboard). Used for "Total Accounts, vs last month" off
 * `new_organizations`, which is the only historical series that bears on a total headcount.
 */
export function growthDelta(monthly: SeriesPointOut[], currentTotal: number): number | null {
  if (monthly.length === 0) return null;
  const last = monthly[monthly.length - 1];
  if (!last || last.value === null) return null;
  const previousTotal = currentTotal - last.value;
  if (previousTotal <= 0) return null;
  return (last.value / previousTotal) * 100;
}
