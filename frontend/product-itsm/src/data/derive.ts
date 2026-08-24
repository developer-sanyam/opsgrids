/**
 * Pure rollups over the incident / request / asset lists. No I/O, no React — just grouping and
 * percentages, so the Overview's KPIs and breakdown charts all derive from the same payloads the
 * tables render. Kept pure so it is trivially testable.
 */
import type {
  Asset,
  Breakdown,
  Incident,
  IncidentCategory,
  Priority,
  ServiceRequest,
} from "@/api/types";
import { incidentCategoryLabel, priorityLabel } from "@/lib/format";

/** An incident still on someone's plate — anything not yet resolved or closed. */
export function isOpen(incident: Incident): boolean {
  return incident.status !== "resolved" && incident.status !== "closed";
}

/** How many incidents are open (new / in progress / on hold). */
export function openIncidentCount(incidents: readonly Incident[]): number {
  return incidents.filter(isOpen).length;
}

/**
 * SLA compliance: the share of incidents whose clock has not been breached, 0–100. A breach is the
 * only failing outcome; met / on-track / at-risk all still count as compliant.
 */
export function slaCompliancePct(incidents: readonly Incident[]): number {
  if (incidents.length === 0) return 100;
  const breached = incidents.filter((i) => i.sla_state === "breached").length;
  return Math.round(((incidents.length - breached) / incidents.length) * 1000) / 10;
}

/** Requests still waiting on an approval step. */
export function requestsAwaitingApproval(requests: readonly ServiceRequest[]): number {
  return requests.filter((r) => r.status === "approval").length;
}

/** Assets the org actively manages — everything not retired. */
export function assetsUnderManagement(assets: readonly Asset[]): number {
  return assets.filter((a) => a.status !== "retired").length;
}

function pct(part: number, whole: number): number {
  return whole === 0 ? 0 : Math.round((part / whole) * 100);
}

const PRIORITY_ORDER: Priority[] = ["P1", "P2", "P3", "P4"];

/** Open-incident count grouped by priority, in P1→P4 order — the breakdown bars. */
export function breakdownByPriority(incidents: readonly Incident[]): Breakdown[] {
  const open = incidents.filter(isOpen);
  const total = open.length;
  return PRIORITY_ORDER.map((p) => {
    const count = open.filter((i) => i.priority === p).length;
    return { key: p, label: priorityLabel(p), count, pct: pct(count, total) };
  }).filter((b) => b.count > 0);
}

const CATEGORY_ORDER: IncidentCategory[] = [
  "network",
  "hardware",
  "software",
  "access",
  "email",
  "security",
];

/** Open-incident count grouped by category, largest first — the breakdown donut. */
export function breakdownByCategory(incidents: readonly Incident[]): Breakdown[] {
  const open = incidents.filter(isOpen);
  const total = open.length;
  return CATEGORY_ORDER.map((c) => {
    const count = open.filter((i) => i.category === c).length;
    return { key: c, label: incidentCategoryLabel(c), count, pct: pct(count, total) };
  })
    .filter((b) => b.count > 0)
    .sort((a, b) => b.count - a.count);
}

/**
 * The "needs attention" queue: open incidents that are either P1 or already breaching SLA. Sorted
 * with breaches first, then by priority, so the most urgent sits at the top.
 */
export function needsAttention(incidents: readonly Incident[]): Incident[] {
  const rank: Record<Priority, number> = { P1: 0, P2: 1, P3: 2, P4: 3 };
  return incidents
    .filter((i) => isOpen(i) && (i.priority === "P1" || i.sla_state === "breached"))
    .sort((a, b) => {
      const breachA = a.sla_state === "breached" ? 0 : 1;
      const breachB = b.sla_state === "breached" ? 0 : 1;
      if (breachA !== breachB) return breachA - breachB;
      return rank[a.priority] - rank[b.priority];
    });
}
