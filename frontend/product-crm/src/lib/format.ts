/**
 * Date and name formatting for OpsGrids Revenue. Money is formatted by `formatMoney` from
 * `@opsgrids/ui`, the one place minor units become a human string.
 */

import { USE_FIXTURES } from "@/api/client";

/** Fixtures are dated around this fixed instant so relative times read sensibly offline — never
 *  reached live, where `nowIso()` returns the real clock instead. */
const FIXTURE_NOW_ISO = "2026-08-24T18:30:00Z";

/**
 * The app's current instant. The one place "now" is read from, so a frozen fixture clock never
 * leaks onto live data: fixture mode gets the fixed instant fixtures were dated around, a real
 * build gets `Date.now()`. Call it at the point you need "now", not once at module load — a
 * `const` computed on import stays stuck at the moment the tab was opened for as long as the
 * session lives.
 */
export function nowIso(): string {
  return USE_FIXTURES ? FIXTURE_NOW_ISO : new Date().toISOString();
}

/** "2026-09-30" -> "30 Sep 2026". Returns "—" for a missing date. */
export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * A relative timestamp against `now` (default the app's fixed NOW): "just now", "3 hr ago",
 * "Yesterday", "5 days ago", then an absolute date past a week.
 */
export function formatRelative(iso: string | null, now: string = nowIso()): string {
  if (!iso) return "—";
  const then = Date.parse(iso);
  const ref = Date.parse(now);
  const mins = Math.round((ref - then) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr ago`;
  const days = Math.round(hrs / 24);
  if (days === 1) return "Yesterday";
  if (days < 7) return `${days} days ago`;
  return formatDate(iso);
}

/** Initials for an avatar fallback: "Sofia Marchetti" -> "SM". */
export function initials(name: string): string {
  const parts = name.trim().split(/\s+/);
  const first = parts[0]?.[0] ?? "";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}
