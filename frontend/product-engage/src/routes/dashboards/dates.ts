/**
 * Calendar-date helpers for the dashboards' range. A range is two `YYYY-MM-DD` strings — the form
 * the analytics endpoints take and the form a `type="date"` input speaks — so nothing here carries
 * a time or a zone, and "today" is the calendar day the person looking is in.
 */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** `2026-09-01` for the local calendar day of `date`. */
export function toIsoDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** The local calendar day `iso` names, or `null` for anything that is not a real date. */
export function parseIsoDate(iso: string): Date | null {
  const match = ISO_DATE.exec(iso);
  if (match === null) return null;
  const [year, month, day] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const date = new Date(year, month - 1, day);
  // `new Date(2026, 1, 31)` rolls over to March; a date that does not survive the round trip
  // was never a date.
  return date.getFullYear() === year && date.getMonth() === month - 1 && date.getDate() === day
    ? date
    : null;
}

export function addDays(date: Date, days: number): Date {
  const next = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  next.setDate(next.getDate() + days);
  return next;
}

/** `2026-09-01` → `1 Sep`. Anything that is not a date is returned as it came. */
export function formatDay(iso: string): string {
  const date = parseIsoDate(iso.slice(0, 10));
  if (date === null) return iso;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

/** `2026-09-01` → `1 Sep 2026`. */
export function formatDayLong(iso: string): string {
  const date = parseIsoDate(iso.slice(0, 10));
  if (date === null) return iso;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
