/**
 * The one client-side rollup left after `GET /itsm/overview` took over the KPIs and breakdowns:
 * the "needs attention" preview on the Overview, which names individual tickets rather than a
 * count and so has no server rollup to draw from.
 */
import type { Incident, Priority } from "@/api/types";

/** An incident still on someone's plate — anything not yet resolved or closed. */
export function isOpen(incident: Incident): boolean {
  return incident.status !== "resolved" && incident.status !== "closed";
}

const PRIORITY_RANK: Record<Priority, number> = { P1: 0, P2: 1, P3: 2, P4: 3 };

/**
 * Open incidents that are either P1 or already breaching SLA, breaches first then by priority.
 * Callers pass a bounded slice of the incident list (the Overview asks for a page sorted by
 * priority) — this is a preview queue with a "View all" link beside it, not an exhaustive count.
 */
export function needsAttention(incidents: readonly Incident[]): Incident[] {
  return incidents
    .filter((i) => isOpen(i) && (i.priority === "P1" || i.sla_state === "breached"))
    .sort((a, b) => {
      const breachA = a.sla_state === "breached" ? 0 : 1;
      const breachB = b.sla_state === "breached" ? 0 : 1;
      if (breachA !== breachB) return breachA - breachB;
      return PRIORITY_RANK[a.priority] - PRIORITY_RANK[b.priority];
    });
}
