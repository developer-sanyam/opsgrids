/**
 * SLA state derivation. Pure, no I/O — turns a ticket's deadline into a labelled state so the UI
 * never conveys SLA health by colour alone. The clock is a fixed instant (`NOW`) matching the
 * seeded fixtures, so what renders is deterministic.
 */
import type { Ticket } from "@/api/types";

/** The fixed "current time" the fixtures are written around. */
export const NOW = new Date("2026-08-24T14:30:00Z");

export type SlaState =
  | "breached" // still open, past its deadline
  | "due_soon" // still open, under an hour of headroom
  | "on_track" // still open, comfortably ahead
  | "paused" // on hold, the clock is stopped
  | "met" // solved within the target
  | "missed"; // solved, but a target slipped along the way

export interface SlaInfo {
  state: SlaState;
  /** A human label that carries the state on its own — read without relying on colour. */
  label: string;
  /** A shorter label for dense table cells. */
  shortLabel: string;
  /** Signed minutes to the deadline (negative once overdue); null when there is no live clock. */
  minutes: number | null;
}

/** "42m", "3h", "3h 10m". */
export function formatDuration(mins: number): string {
  const m = Math.abs(Math.round(mins));
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r === 0 ? `${h}h` : `${h}h ${r}m`;
}

export function slaInfo(ticket: Ticket, now: Date = NOW): SlaInfo {
  if (ticket.status === "solved") {
    return ticket.sla_breached
      ? { state: "missed", label: "SLA missed", shortLabel: "Missed", minutes: null }
      : { state: "met", label: "SLA met", shortLabel: "Met", minutes: null };
  }
  if (ticket.status === "on_hold") {
    return { state: "paused", label: "Clock paused", shortLabel: "Paused", minutes: null };
  }
  if (!ticket.sla_due) {
    return { state: "on_track", label: "No target set", shortLabel: "—", minutes: null };
  }
  const minutes = (new Date(ticket.sla_due).getTime() - now.getTime()) / 60000;
  if (ticket.sla_breached || minutes < 0) {
    const over = formatDuration(minutes);
    return { state: "breached", label: `Breached ${over} ago`, shortLabel: `−${over}`, minutes };
  }
  const left = formatDuration(minutes);
  if (minutes <= 60) {
    return { state: "due_soon", label: `Due in ${left}`, shortLabel: left, minutes };
  }
  return { state: "on_track", label: `Due in ${left}`, shortLabel: left, minutes };
}

/** Whether this ticket needs attention now: open and either breached or under an hour of headroom. */
export function needsAttention(ticket: Ticket, now: Date = NOW): boolean {
  const info = slaInfo(ticket, now);
  return info.state === "breached" || info.state === "due_soon";
}
