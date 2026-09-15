/**
 * The Overview metrics fixture. Counts (active / trial / suspended / locked, MRR) are computed off
 * the directory so the dashboard and the accounts table can never disagree; the trend series and
 * the rate metrics (trial-conversion, churn) are coherent hand-set projections — exactly the shape
 * the console's event-stream projection would emit (ADR-0004 §4).
 */

import type { BreakdownSegment, MrrPoint, OverviewMetrics } from "@/api/types";
import { rupees } from "./catalog";
import { ORGANIZATIONS } from "./orgs";

const MONTH_LABELS = [
  "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug",
];

// MRR trailing twelve months, in whole rupees, ending at the directory's current MRR.
const MRR_TREND_RUPEES = [
  1_240_000, 1_310_000, 1_370_000, 1_420_000, 1_470_000, 1_515_000, 1_555_000, 1_595_000,
  1_625_000, 1_660_000, 1_695_000, 1_760_000,
];

function countStatus(status: string): number {
  return ORGANIZATIONS.filter((o) => o.status === status).length;
}

export function buildOverviewMetrics(): OverviewMetrics {
  const mrrMinor = ORGANIZATIONS.reduce((sum, o) => sum + o.mrr_minor, 0);
  const activeAccounts = countStatus("active");
  const trials = countStatus("trial");
  const suspended = countStatus("suspended");
  const locked = ORGANIZATIONS.filter((o) => o.locked).length;

  const mrrSeries: MrrPoint[] = MRR_TREND_RUPEES.map((whole, i) => ({
    label: MONTH_LABELS[i] ?? "",
    mrr_minor: rupees(whole),
  }));

  const trialFunnel: BreakdownSegment[] = [
    { key: "converted", label: "Converted to paid", count: 8, tone: "active" },
    { key: "in_trial", label: "Still in trial", count: 3, tone: "trial" },
    { key: "expired", label: "Trial expired", count: 1, tone: "cancelled" },
  ];

  const churnBreakdown: BreakdownSegment[] = [
    { key: "retained", label: "Retained (active)", count: activeAccounts, tone: "active" },
    { key: "past_due", label: "Past due", count: countStatus("past_due"), tone: "pastdue" },
    { key: "suspended", label: "Suspended", count: suspended, tone: "suspended" },
    { key: "cancelled", label: "Cancelled", count: countStatus("cancelled"), tone: "cancelled" },
  ];

  return {
    currency: "INR",
    period_label: "August 2026",

    mrr_minor: mrrMinor,
    mrr_delta_pct: 3.8,
    mrr_sparkline: MRR_TREND_RUPEES.slice(-7),
    mrr_series: mrrSeries,

    active_accounts: activeAccounts,
    active_delta_pct: 2.2,
    active_sparkline: [5, 6, 6, 7, 7, 8, activeAccounts],

    trials_in_progress: trials,
    trials_delta_pct: 12.5,
    trials_sparkline: [1, 2, 2, 2, 3, 2, trials],

    trial_conversion_pct: 66.7,
    conversion_delta_pct: 4.5,
    conversion_sparkline: [58, 60, 61, 63, 64, 65, 67],

    churn_pct: 2.4,
    churn_delta_pct: -0.5,
    churn_sparkline: [3.1, 3.0, 2.9, 2.8, 2.7, 2.6, 2.4],

    locked_accounts: locked,
    suspended_accounts: suspended,

    revenue_period_minor: rupees(1_715_000),
    revenue_delta_pct: 5.1,

    trial_funnel: trialFunnel,
    churn_breakdown: churnBreakdown,
  };
}
