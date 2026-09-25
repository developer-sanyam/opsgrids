/**
 * Platform metrics fixture — `MetricsOut` / `MetricsOverviewOut`, computed off the same directory
 * fixture the accounts screens read, so the two can never disagree. There is no historical series,
 * no period-over-period delta and no revenue-this-period figure here on purpose: the real
 * `GET /admin/metrics/overview` payload doesn't carry them either (see `types.ts`).
 */

import type {
  LifecycleCountsOut,
  MetricsOut,
  MetricsOverviewOut,
  MetricsSeriesOut,
  SeriesPointOut,
} from "@/api/types";
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
    // Fractions rounded to 4 places, exactly as console's `platform_metrics` sends them.
    trial_conversion_rate: Math.round((trialConversions / trialsStarted) * 10_000) / 10_000,
    active_subscriptions: activeSubscriptions,
    churned: cancelledCount,
    churn_rate: Math.round((cancelledCount / (states.length || 1)) * 10_000) / 10_000,
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

/** `"2026-09-15T00:00:00Z"` -> `"2026-09"`. */
function monthKey(iso: string): string {
  return iso.slice(0, 7);
}

/** The trailing `months` calendar months ending with the current one, oldest first — the same
 *  sequence `domain.month_sequence` builds server-side. */
function monthSequence(months: number, end: Date = new Date()): string[] {
  const out: string[] = [];
  for (let offset = months - 1; offset >= 0; offset -= 1) {
    const d = new Date(Date.UTC(end.getUTCFullYear(), end.getUTCMonth() - offset, 1));
    out.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
  }
  return out;
}

/**
 * `GET /admin/metrics/series?months=` — monthly account growth and commercial history. Derived
 * from the same seed every other fixture reads, so the Account Growth chart, the KPI deltas and
 * the accounts list can never disagree with one another.
 *
 * Every subscription in this seed starts as a trial (the platform's real shape today — see the
 * owner brief): `trials_started` counts subscriptions created that month, `trial_conversions`
 * counts the ones now `active`/`past_due`, `churned` the ones now `cancelled`. A month before the
 * earliest organization in the seed existed carries `null`, never a fabricated `0` — the same rule
 * the live series applies to a month before console's own tracking window began.
 */
export function buildMetricsSeries(months: number): MetricsSeriesOut {
  const orgs = buildOrgStates();
  const earliest = orgs
    .map((o) => o.created_at)
    .filter((iso): iso is string => iso !== null)
    .sort()[0];
  const periods = monthSequence(months);

  function points(valueFor: (period: string) => number): SeriesPointOut[] {
    return periods.map((period) => ({
      period,
      value: earliest !== undefined && period < monthKey(earliest) ? null : valueFor(period),
    }));
  }

  const newOrgsByMonth = new Map<string, number>();
  for (const org of orgs) {
    if (!org.created_at) continue;
    const key = monthKey(org.created_at);
    newOrgsByMonth.set(key, (newOrgsByMonth.get(key) ?? 0) + 1);
  }

  const trialsByMonth = new Map<string, number>();
  const conversionsByMonth = new Map<string, number>();
  const churnedByMonth = new Map<string, number>();
  const mrrByMonth = new Map<string, number>();
  for (const sub of SUBSCRIPTIONS) {
    const key = monthKey(sub.created_at);
    trialsByMonth.set(key, (trialsByMonth.get(key) ?? 0) + 1);
    if (sub.state === "active" || sub.state === "past_due") {
      conversionsByMonth.set(key, (conversionsByMonth.get(key) ?? 0) + 1);
      mrrByMonth.set(key, (mrrByMonth.get(key) ?? 0) + sub.amount_minor);
    }
    if (sub.state === "cancelled") {
      churnedByMonth.set(key, (churnedByMonth.get(key) ?? 0) + 1);
    }
  }

  // MRR is a running total, not a per-month flow: once a subscription starts contributing, it
  // keeps contributing to every later month's figure until it churns (this seed has no
  // mid-lifetime cancellations to net back out).
  let running = 0;
  const mrrCumulative = new Map<string, number>();
  for (const period of periods) {
    running += mrrByMonth.get(period) ?? 0;
    mrrCumulative.set(period, running);
  }

  return {
    currency: "INR",
    mrr_minor: points((p) => mrrCumulative.get(p) ?? 0),
    new_organizations: points((p) => newOrgsByMonth.get(p) ?? 0),
    trials_started: points((p) => trialsByMonth.get(p) ?? 0),
    trial_conversions: points((p) => conversionsByMonth.get(p) ?? 0),
    churned: points((p) => churnedByMonth.get(p) ?? 0),
  };
}
