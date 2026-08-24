/**
 * Pure rollups over the ticket list. No I/O, no React — just counting, grouping and percentages,
 * so the Overview's headline numbers and breakdowns all derive from the one `GET /tickets` payload.
 * Kept pure so it is trivially testable and never drifts from what the Queue shows.
 */
import type {
  Agent,
  BreakdownSlice,
  Priority,
  Ticket,
  TicketStatus,
} from "@/api/types";
import { NOW, slaInfo } from "@/lib/sla";

const PRIORITY_ORDER: Priority[] = ["urgent", "high", "normal", "low"];
const PRIORITY_LABEL: Record<Priority, string> = {
  urgent: "Urgent",
  high: "High",
  normal: "Normal",
  low: "Low",
};

const STATUS_ORDER: TicketStatus[] = ["new", "open", "pending", "on_hold", "solved"];
const STATUS_LABEL: Record<TicketStatus, string> = {
  new: "New",
  open: "Open",
  pending: "Pending",
  on_hold: "On Hold",
  solved: "Solved",
};

/** A ticket is "active" until it is solved — everything still in the queue. */
export function isActive(ticket: Ticket): boolean {
  return ticket.status !== "solved";
}

export function openCount(tickets: readonly Ticket[]): number {
  return tickets.filter(isActive).length;
}

/** Active tickets whose SLA clock has already been missed. */
export function breachingCount(tickets: readonly Ticket[], now: Date = NOW): number {
  return tickets.filter((t) => slaInfo(t, now).state === "breached").length;
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid]!;
  return Math.round((sorted[mid - 1]! + sorted[mid]!) / 2);
}

/** Median minutes from ticket creation to first agent reply, across tickets that have replied. */
export function medianFirstResponseMin(tickets: readonly Ticket[]): number {
  const spans = tickets
    .filter((t) => t.first_response_at !== null)
    .map(
      (t) =>
        (new Date(t.first_response_at as string).getTime() -
          new Date(t.created_at).getTime()) /
        60000,
    );
  return median(spans);
}

/** CSAT: share of rated, solved tickets marked "good", 0–100. Null when nothing is rated yet. */
export function csatPct(tickets: readonly Ticket[]): number | null {
  const rated = tickets.filter((t) => t.satisfaction !== null);
  if (rated.length === 0) return null;
  const good = rated.filter((t) => t.satisfaction === "good").length;
  return Math.round((good / rated.length) * 100);
}

function toSlices<K extends string>(
  order: readonly K[],
  label: Record<K, string>,
  counts: Map<K, number>,
  total: number,
): BreakdownSlice[] {
  return order
    .map((key) => {
      const count = counts.get(key) ?? 0;
      return {
        key,
        label: label[key],
        count,
        pct: total === 0 ? 0 : Math.round((count / total) * 100),
      };
    })
    .filter((slice) => slice.count > 0);
}

/** Active tickets grouped by priority, in severity order. */
export function priorityBreakdown(tickets: readonly Ticket[]): BreakdownSlice[] {
  const active = tickets.filter(isActive);
  const counts = new Map<Priority, number>();
  for (const t of active) counts.set(t.priority, (counts.get(t.priority) ?? 0) + 1);
  return toSlices(PRIORITY_ORDER, PRIORITY_LABEL, counts, active.length);
}

/** All tickets grouped by lifecycle status, in pipeline order. */
export function statusBreakdown(tickets: readonly Ticket[]): BreakdownSlice[] {
  const counts = new Map<TicketStatus, number>();
  for (const t of tickets) counts.set(t.status, (counts.get(t.status) ?? 0) + 1);
  return toSlices(STATUS_ORDER, STATUS_LABEL, counts, tickets.length);
}

/**
 * The "needs attention" list: active tickets that are breached or within an hour of their deadline,
 * most overdue first. This is what an agent should pick up next.
 */
export function needsAttentionList(tickets: readonly Ticket[], now: Date = NOW): Ticket[] {
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
