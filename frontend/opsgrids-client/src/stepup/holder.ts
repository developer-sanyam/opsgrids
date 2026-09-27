/**
 * The step-up token's home: one module-scope variable, and nothing else.
 *
 * A step-up token is proof that the person typed a fresh authenticator code a moment ago. It is
 * more sensitive than the session token in one way that matters — it is what unmasks PII — and so
 * it is held more strictly: **memory only.** Never `sessionStorage` (which `session.ts` uses for
 * the session token so a reload stays signed in), never `localStorage`, never a cookie. A reload
 * loses it on purpose; the price is one more code.
 *
 * It is dropped by every one of these, whichever comes first:
 * - its own lifetime, by a timer *and* by a check on every read — timers are throttled and
 *   suspended in background tabs and across sleep, so the deadline is also compared against the
 *   clock when the token is asked for;
 * - `clearStepUpToken()`, which the hook calls on navigation and the app can call when a
 *   drawer or dialog that revealed something closes;
 * - the session ending (`AuthProvider`'s sign-out, an unauthorized 401, a failed refresh).
 *
 * The deadline is the client's clock plus `expires_in`, never the token's own `exp` claim — the
 * same rule the session's proactive refresh follows, for the same reason: a skewed clock must not
 * turn "valid for three minutes" into "already expired" or "valid for an hour".
 */

/**
 * The token is treated as gone this long before the server would say so, so one that is about to
 * lapse is never sent on a request that lands after it has.
 */
const EXPIRY_MARGIN_MS = 5_000;

let held: { token: string; expiresAt: number } | null = null;
let timer: ReturnType<typeof setTimeout> | null = null;

/** Drop the token now, and the timer that would have. Safe to call when nothing is held. */
export function clearStepUpToken(): void {
  held = null;
  if (timer !== null) {
    clearTimeout(timer);
    timer = null;
  }
}

/**
 * Hold `token` for `expiresInSeconds` (from the response's `expires_in`), replacing any other.
 * A lifetime too short to survive the safety margin holds nothing: the caller still uses the
 * token it was just given, once, and the next reveal asks again.
 */
export function setStepUpToken(token: string, expiresInSeconds: number): void {
  clearStepUpToken();
  const lifetimeMs = expiresInSeconds * 1000 - EXPIRY_MARGIN_MS;
  if (!Number.isFinite(lifetimeMs) || lifetimeMs <= 0) return;
  held = { token, expiresAt: Date.now() + lifetimeMs };
  timer = setTimeout(clearStepUpToken, lifetimeMs);
}

/** The held token, or `null` when there is none or it has run out. */
export function getStepUpToken(): string | null {
  if (held === null) return null;
  if (Date.now() >= held.expiresAt) {
    clearStepUpToken();
    return null;
  }
  return held.token;
}
