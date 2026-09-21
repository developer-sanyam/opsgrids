/**
 * Pure UTC date helpers. Used by both clocks in `client.ts` — the pinned fixture clock in
 * `fixtures.ts` and the real one derived from `new Date()` — so there is exactly one
 * implementation of "what's the Monday of this week" to get right.
 */

/** UTC YYYY-MM-DD for a `Date`. */
export function isoOf(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Monday of the week containing `iso`. */
export function mondayOf(iso: string): string {
  const base = new Date(`${iso}T00:00:00Z`);
  const dow = (base.getUTCDay() + 6) % 7; // 0 = Monday
  base.setUTCDate(base.getUTCDate() - dow);
  return isoOf(base);
}

/** The seven ISO dates of the week starting `mondayIso`, Monday first. */
export function weekFrom(mondayIso: string): string[] {
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(`${mondayIso}T00:00:00Z`);
    d.setUTCDate(d.getUTCDate() + i);
    return isoOf(d);
  });
}

/** `iso` shifted by `days` (negative walks backward). Used for week/day navigation controls. */
export function addDays(iso: string, days: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return isoOf(d);
}

/** The Monday `weeks` away from `mondayIso` — the week navigation's prev/next. */
export function addWeeks(mondayIso: string, weeks: number): string {
  return addDays(mondayIso, weeks * 7);
}
