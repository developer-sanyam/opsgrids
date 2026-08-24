import type { Channel, Priority, TicketStatus } from "@/api/types";

/**
 * "2026-08-24T09:24:00Z" -> "9:24 AM". Returns "—" for a missing time.
 * Rendered in UTC so a stored wall-clock time shows as-is, independent of the viewer's zone.
 */
export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleTimeString("en-IN", {
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

/** "2026-08-24" -> "24 Aug 2026". */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** "2026-08-24T09:24:00Z" -> "24 Aug, 9:24 AM". */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  const date = d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  });
  return `${date}, ${formatTime(iso)}`;
}

/** A compact "time ago / from now" against a reference instant. "12m ago" / "in 2h". */
export function relativeTime(iso: string | null, now: Date): string {
  if (!iso) return "—";
  const diffMin = Math.round((new Date(iso).getTime() - now.getTime()) / 60000);
  const past = diffMin <= 0;
  const m = Math.abs(diffMin);
  let span: string;
  if (m < 1) span = "just now";
  else if (m < 60) span = `${m}m`;
  else if (m < 60 * 24) {
    const h = Math.floor(m / 60);
    span = `${h}h`;
  } else {
    const d = Math.floor(m / (60 * 24));
    span = `${d}d`;
  }
  if (span === "just now") return span;
  return past ? `${span} ago` : `in ${span}`;
}

/** 512 -> "8h 32m". */
export function formatMinutes(minutes: number | null): string {
  if (minutes === null) return "—";
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  if (h === 0) return `${m}m`;
  return `${h}h ${m.toString().padStart(2, "0")}m`;
}

const PRIORITY_LABELS: Record<Priority, string> = {
  urgent: "Urgent",
  high: "High",
  normal: "Normal",
  low: "Low",
};

export function priorityLabel(priority: Priority): string {
  return PRIORITY_LABELS[priority];
}

const STATUS_LABELS: Record<TicketStatus, string> = {
  new: "New",
  open: "Open",
  pending: "Pending",
  on_hold: "On Hold",
  solved: "Solved",
};

export function statusLabel(status: TicketStatus): string {
  return STATUS_LABELS[status];
}

const CHANNEL_LABELS: Record<Channel, string> = {
  email: "Email",
  chat: "Live chat",
  web: "Web form",
  phone: "Phone",
  api: "API",
};

export function channelLabel(channel: Channel): string {
  return CHANNEL_LABELS[channel];
}

/** Initials for an avatar fallback: "Ananya Iyer" -> "AI". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Format a delta like 4.2 -> "+4.2%" / -1.4 -> "−1.4%". */
export function formatDelta(pct: number): string {
  const sign = pct >= 0 ? "+" : "−";
  return `${sign}${Math.abs(pct).toFixed(1)}%`;
}
