/**
 * Values revealed in this session, held in memory so that coming back to a record does not ask
 * the server (and write an audit row) for a number the person has already been shown.
 *
 * **Memory only.** A module-scope map: never `localStorage`, never `sessionStorage`, never a
 * cookie, never the URL. A reload loses it on purpose; the price is one more tap.
 *
 * Keyed by what the value belongs to (`lead:<id>:phone number`), and emptied by the same events
 * that end the access: sign-out, a 401, a failed refresh, and a new sign-in (another person or
 * another organization on the same tab). `AuthProvider` calls `clearRevealedValues()` beside
 * every place it drops the session. There is no timer: since 2026-10-15 a revealed value stays
 * visible for the session (owner decision, ADR-0023 addendum).
 */

const revealed = new Map<string, string>();

export function getRevealedValue(key: string): string | null {
  return revealed.get(key) ?? null;
}

export function setRevealedValue(key: string, value: string): void {
  revealed.set(key, value);
}

/** Drop every value now. Safe to call when nothing is held. */
export function clearRevealedValues(): void {
  revealed.clear();
}
