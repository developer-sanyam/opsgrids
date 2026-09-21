/**
 * Pure rollups over a ticket list. No I/O, no React.
 *
 * The Overview's headline KPIs and breakdowns come from `GET /support/overview`
 * (`services/support/app/domain.py` computes the identical rollups server-side, over every
 * ticket in the organization) — this module no longer duplicates that arithmetic client-side
 * over a capped page of tickets, which is what silently under-counted a queue past 100 rows.
 * What is left here is what has no server endpoint of its own: SLA-derived "needs attention" and
 * resolving an assignee id to its agent record.
 */
import type { Agent, Ticket } from "@/api/types";
import { now as clockNow } from "@/lib/clock";
import { slaInfo } from "@/lib/sla";

/**
 * The "needs attention" list: active tickets that are breached or within an hour of their
 * deadline, most overdue first. This is what an agent should pick up next.
 */
export function needsAttentionList(tickets: readonly Ticket[], now: Date = clockNow()): Ticket[] {
  return tickets
    .filter((t) => {
      const state = slaInfo(t, now).state;
      return state === "breached" || state === "due_soon";
    })
    .sort((a, b) => {
      const am = slaInfo(a, now).minutes ?? 0;
      const bm = slaInfo(b, now).minutes ?? 0;
      return am - bm;
    });
}

/** Resolve an agent id to its record. */
export function agentById(agents: readonly Agent[], id: string | null): Agent | null {
  if (id === null) return null;
  return agents.find((a) => a.id === id) ?? null;
}
