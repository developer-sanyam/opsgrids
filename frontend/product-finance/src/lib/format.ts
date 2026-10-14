/**
 * Formatting helpers shared across the Finance screens — dates, deltas, initials and day maths.
 * Money lives in its own module (`money.ts`) because it has stricter rules.
 */

import { formatInstant } from "@opsgrids/ui";

/** "2026-08-24" -> "24 Aug 2026". Rendered in UTC so a stored date shows as-is. */
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

/** "2026-08-24" -> "24 Aug". A compact date for dense rows where the year is implied. */
export function formatDateShort(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    timeZone: "UTC",
  });
}

/**
 * An ISO instant -> "24 Aug, 2:54 pm IST". An instant renders in the viewer's own zone, and the
 * zone's name is part of the text, so two people reading one deadline in different zones can see
 * that they are reading the same moment. A calendar date goes through `formatDate` instead.
 */
export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return formatInstant(iso, null, { locations: {}, organizationZone: zone }, "datetime").value;
}

/** Whole days between two ISO dates (b − a). Negative when `b` is before `a`. */
export function daysBetween(aIso: string, bIso: string): number {
  const a = Date.parse(`${aIso}T00:00:00Z`);
  const b = Date.parse(`${bIso}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}

/**
 * The real device clock, as `YYYY-MM-DD`. The one place live code reads "today" — never a frozen
 * fixture constant, which is only ever imported from a component gated on fixture mode. Takes an
 * optional injection point for a test that needs a fixed instant.
 */
export function today(clock: () => Date = () => new Date()): string {
  return clock().toISOString().slice(0, 10);
}
