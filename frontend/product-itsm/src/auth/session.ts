/**
 * The access-token store — deliberately outside React so the typed API client can read the
 * current token without importing hooks. The token lives in memory and is mirrored to
 * `sessionStorage` so a reload within the same tab stays signed in; it is **never** written to
 * `localStorage`, which would outlive the tab and widen the blast radius of an XSS payload. The
 * long-lived refresh token is an httpOnly cookie the browser holds and JavaScript cannot read.
 */

const STORAGE_KEY = "atmora.itsm.access_token";

let inMemoryToken: string | null = null;
let unauthorizedHandler: (() => void) | null = null;

/** The current access token, rehydrated from `sessionStorage` on first read after a reload. */
export function getAccessToken(): string | null {
  if (inMemoryToken !== null) return inMemoryToken;
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (stored) {
      inMemoryToken = stored;
      return stored;
    }
  } catch {
    // sessionStorage can throw in private modes — treat as "no token".
  }
  return null;
}

/** Set (or clear, with `null`) the token in memory and mirror it to `sessionStorage`. */
export function setAccessToken(token: string | null): void {
  inMemoryToken = token;
  try {
    if (token) sessionStorage.setItem(STORAGE_KEY, token);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Persistence is best-effort; the in-memory copy still drives the session.
  }
}

/**
 * Register the callback the client fires on a `401` from any gateway call. The provider uses it
 * to drop the session and bounce back to the login screen.
 */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

/** Called by the client when the gateway rejects the token. Clears it, then notifies the app. */
export function notifyUnauthorized(): void {
  setAccessToken(null);
  unauthorizedHandler?.();
}
