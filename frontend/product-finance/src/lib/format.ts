/**
 * Formatting helpers shared across the Finance screens — dates, deltas, initials and day maths.
 * Money lives in its own module (`money.ts`) because it has stricter rules.
 */

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

/** Whole days between two ISO dates (b − a). Negative when `b` is before `a`. */
export function daysBetween(aIso: string, bIso: string): number {
  const a = Date.parse(`${aIso}T00:00:00Z`);
  const b = Date.parse(`${bIso}T00:00:00Z`);
  return Math.round((b - a) / 86_400_000);
}
