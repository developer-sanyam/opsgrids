import type {
  AssetStatus,
  AssetType,
  CatalogCategory,
  IncidentCategory,
  IncidentStatus,
  Priority,
  RequestStatus,
  SlaState,
} from "@/api/types";
import { now } from "./clock";

/** "2026-08-24T09:24:00Z" -> "24 Aug, 9:24 AM". Rendered in UTC so a stored time shows as-is. */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-IN", {
    day: "2-digit",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
    timeZone: "UTC",
  });
}

/** "2021-03-15" -> "15 Mar 2021". */
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

/**
 * A compact relative label against the real clock (`@/lib/clock`'s `now()` — pinned only in
 * fixture mode). "3h ago", "2d ago", "just now". Keeps the worklog and tables reading like a
 * live desk instead of freezing the moment an incident is raised.
 */
export function relativeTime(iso: string, nowMs: number = now().getTime()): string {
  const then = Date.parse(iso);
  const diffMin = Math.round((nowMs - then) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 24) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  if (diffDay < 30) return `${diffDay}d ago`;
  const diffMon = Math.round(diffDay / 30);
  return `${diffMon}mo ago`;
}

/**
 * Signed minutes remaining on an SLA clock rendered as a human duration:
 * 128 -> "2h 8m left", -45 -> "45m over", 0/null -> "—".
 */
export function formatSlaClock(minutes: number | null): string {
  if (minutes === null) return "—";
  const over = minutes < 0;
  const abs = Math.abs(minutes);
  const h = Math.floor(abs / 60);
  const m = abs % 60;
  const span = h > 0 ? `${h}h ${m.toString().padStart(2, "0")}m` : `${m}m`;
  return over ? `${span} over` : `${span} left`;
}

/** 12500000 minor units of "INR" -> "₹1,25,000". No decimals when the amount is whole. */
export function formatMoney(minor: number, currency: string): string {
  const major = minor / 100;
  try {
    return new Intl.NumberFormat("en-IN", {
      style: "currency",
      currency,
      maximumFractionDigits: major % 1 === 0 ? 0 : 2,
    }).format(major);
  } catch {
    // An unknown ISO code shouldn't crash a cell.
    return `${currency} ${major.toLocaleString("en-IN")}`;
  }
}

/** Today, against the real clock: "Mon, 24 Aug 2026". */
export function todayLabel(reference: Date = now()): string {
  return reference.toLocaleDateString("en-IN", {
    weekday: "short",
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/** Whole days from `reference` to a stored ISO date, negative once it has passed. */
export function daysUntil(iso: string, reference: Date = now()): number {
  const target = Date.parse(`${iso}T00:00:00Z`);
  return Math.round((target - reference.getTime()) / 86_400_000);
}

/* ------------------------------- label maps ------------------------------- */

const PRIORITY_LABELS: Record<Priority, string> = {
  P1: "P1 · Critical",
  P2: "P2 · High",
  P3: "P3 · Moderate",
  P4: "P4 · Low",
};
export function priorityLabel(p: Priority): string {
  return PRIORITY_LABELS[p];
}

const INCIDENT_STATUS_LABELS: Record<IncidentStatus, string> = {
  new: "New",
  in_progress: "In progress",
  on_hold: "On hold",
  resolved: "Resolved",
  closed: "Closed",
};
export function incidentStatusLabel(s: IncidentStatus): string {
  return INCIDENT_STATUS_LABELS[s];
}

const SLA_LABELS: Record<SlaState, string> = {
  met: "Met",
  on_track: "On track",
  at_risk: "At risk",
  breached: "Breached",
};
export function slaLabel(s: SlaState): string {
  return SLA_LABELS[s];
}

const INCIDENT_CATEGORY_LABELS: Record<IncidentCategory, string> = {
  network: "Network",
  hardware: "Hardware",
  software: "Software",
  access: "Access",
  email: "Email",
  security: "Security",
};
export function incidentCategoryLabel(c: IncidentCategory): string {
  return INCIDENT_CATEGORY_LABELS[c];
}

const CATALOG_CATEGORY_LABELS: Record<CatalogCategory, string> = {
  onboarding: "Onboarding",
  offboarding: "Offboarding",
  access: "Access",
  hardware: "Hardware",
  software: "Software",
};
export function catalogCategoryLabel(c: CatalogCategory): string {
  return CATALOG_CATEGORY_LABELS[c];
}

const REQUEST_STATUS_LABELS: Record<RequestStatus, string> = {
  approval: "Awaiting approval",
  in_progress: "In progress",
  fulfilled: "Fulfilled",
  rejected: "Rejected",
};
export function requestStatusLabel(s: RequestStatus): string {
  return REQUEST_STATUS_LABELS[s];
}

const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  laptop: "Laptop",
  desktop: "Desktop",
  server: "Server",
  monitor: "Monitor",
  phone: "Phone",
  network: "Network",
  license: "License",
};
export function assetTypeLabel(t: AssetType): string {
  return ASSET_TYPE_LABELS[t];
}

const ASSET_STATUS_LABELS: Record<AssetStatus, string> = {
  in_use: "In use",
  in_stock: "In stock",
  in_repair: "In repair",
  retired: "Retired",
};
export function assetStatusLabel(s: AssetStatus): string {
  return ASSET_STATUS_LABELS[s];
}
