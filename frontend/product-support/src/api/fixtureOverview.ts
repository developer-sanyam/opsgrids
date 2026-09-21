/**
 * Fixture-mode stand-in for `GET /support/overview`. Mirrors
 * `services/support/app/domain.py`'s `build_overview` closely enough that a screen built against
 * fixtures renders the same shape it will against the real rollup — computed over every fixture
 * ticket, not a capped page of them.
 */
import { FIXTURE_NOW } from "./fixtures";
import type { BreakdownSlice, Overview, Priority, Ticket, TicketStatus, VolumePoint } from "./types";

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

const WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function isActive(ticket: Ticket): boolean {
  return ticket.status !== "solved";
}

function median(values: number[]): number {
  if (values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 1
    ? Math.round(sorted[mid]!)
    : Math.round((sorted[mid - 1]! + sorted[mid]!) / 2);
}

function slices<K extends string>(
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

function isoDate(iso: string): string {
  return iso.slice(0, 10);
}

function volumeSeries(tickets: readonly Ticket[], now: Date, days = 7): VolumePoint[] {
  const today = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
  const start = new Date(today);
  start.setUTCDate(start.getUTCDate() - (days - 1));

  const opened = new Map<string, number>();
  const solved = new Map<string, number>();
  for (const t of tickets) {
    const created = isoDate(t.created_at);
    if (created >= isoDate(start.toISOString()) && created <= isoDate(today.toISOString())) {
      opened.set(created, (opened.get(created) ?? 0) + 1);
    }
    if (t.status === "solved") {
      const updated = isoDate(t.updated_at);
      if (updated >= isoDate(start.toISOString()) && updated <= isoDate(today.toISOString())) {
        solved.set(updated, (solved.get(updated) ?? 0) + 1);
      }
    }
  }

  const points: VolumePoint[] = [];
  for (let i = 0; i < days; i += 1) {
    const d = new Date(start);
    d.setUTCDate(d.getUTCDate() + i);
    const iso = d.toISOString().slice(0, 10);
    points.push({
      label: WEEKDAY[d.getUTCDay() === 0 ? 6 : d.getUTCDay() - 1]!,
      date: iso,
      opened: opened.get(iso) ?? 0,
      solved: solved.get(iso) ?? 0,
    });
  }
  return points;
}

export function buildFixtureOverview(tickets: readonly Ticket[], now: Date = FIXTURE_NOW): Overview {
  const active = tickets.filter(isActive);
  const priorityCounts = new Map<Priority, number>();
  for (const t of active) priorityCounts.set(t.priority, (priorityCounts.get(t.priority) ?? 0) + 1);

  const statusCounts = new Map<TicketStatus, number>();
  for (const t of tickets) statusCounts.set(t.status, (statusCounts.get(t.status) ?? 0) + 1);

  const responseSpans = tickets
    .filter((t) => t.first_response_at !== null)
    .map(
      (t) =>
        (new Date(t.first_response_at as string).getTime() - new Date(t.created_at).getTime()) /
        60000,
    );

  const rated = tickets.filter((t) => t.satisfaction !== null);
  const csatPct =
    rated.length === 0
      ? null
      : Math.round((rated.filter((t) => t.satisfaction === "good").length / rated.length) * 100);

  return {
    open_tickets: active.length,
    breaching_sla: active.filter((t) => t.sla_breached).length,
    first_response_median_min: median(responseSpans),
    csat_pct: csatPct,
    volume: volumeSeries(tickets, now),
    priority_breakdown: slices(PRIORITY_ORDER, PRIORITY_LABEL, priorityCounts, active.length),
    status_breakdown: slices(STATUS_ORDER, STATUS_LABEL, statusCounts, tickets.length),
  };
}
