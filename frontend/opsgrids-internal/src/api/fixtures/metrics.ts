/**
 * Platform metrics fixture — `MetricsOut` / `MetricsOverviewOut`, computed off the same directory
 * fixture the accounts screens read, so the two can never disagree. There is no historical series,
 * no period-over-period delta and no revenue-this-period figure here on purpose: the real
 * `GET /admin/metrics/overview` payload doesn't carry them either (see `types.ts`).
 */

import type { LifecycleCountsOut, MetricsOut, MetricsOverviewOut } from "@/api/types";
import { buildOrgStates, SUBSCRIPTIONS } from "./orgs";

function countByStatus(statuses: string[], status: string): number {
  return statuses.filter((s) => s === status).length;
}

export function buildPlatformMetrics(): MetricsOut {
  const states = buildOrgStates();
  const mrrMinor = states.reduce((sum, s) => sum + s.mrr_minor, 0);
  const activeSubscriptions = SUBSCRIPTIONS.filter((s) => s.state === "active").length;
  const trialCount = SUBSCRIPTIONS.filter((s) => s.state === "trial").length;
  const cancelledCount = SUBSCRIPTIONS.filter((s) => s.state === "cancelled").length;
  // Trailing-90-day trial funnel: the trials seeded as still-in-progress, plus a fixed count of
  // ones assumed to have already converted or expired — a fixtures-only approximation, not a claim
  // about the live rate.
  const trialsStarted = trialCount + 9;
  const trialConversions = 8;
  return {
    mrr_minor: mrrMinor,
    currency: "INR",
    trials_started: trialsStarted,
    trial_conversions: trialConversions,
    trial_conversion_rate: Math.round((trialConversions / trialsStarted) * 1000) / 10,
    active_subscriptions: activeSubscriptions,
    churned: cancelledCount,
    churn_rate: Math.round((cancelledCount / (states.length || 1)) * 1000) / 10,
  };
}

export function buildLifecycleCounts(): LifecycleCountsOut {
  const states = buildOrgStates();
  const statuses = states.map((s) => {
    if (s.locked) return "suspended";
    if (s.trial_ends_at) return "trial";
    return "active";
  });
  // The seed encodes "cancelled" and "past_due" only at the subscription level (`SUBSCRIPTIONS`),
  // not on `OrgStateOut` — there is no status field there in the live contract either.
  const cancelled = SUBSCRIPTIONS.filter((s) => s.state === "cancelled").length;
  return {
    suspended: countByStatus(statuses, "suspended"),
    active: countByStatus(statuses, "active"),
    trial: countByStatus(statuses, "trial"),
    churned: cancelled,
    none: 0,
  };
}

export function buildOverviewMetrics(): MetricsOverviewOut {
  const lifecycle = buildLifecycleCounts();
  return {
    ...buildPlatformMetrics(),
    organization_count: buildOrgStates().length,
    organizations_by_lifecycle: lifecycle,
  };
}
