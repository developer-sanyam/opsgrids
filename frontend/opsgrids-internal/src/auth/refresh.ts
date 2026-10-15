/**
 * The operator console's silent refresh: `POST /api/v1/auth/operator/refresh`, which rotates the
 * httpOnly refresh cookie auth-service set at sign-in and answers with a fresh access token.
 *
 * The access token lives in memory and `sessionStorage` only, so a new browser run, a new tab and
 * the token's own 15-minute expiry all leave the console without one. The cookie survives all
 * three (it carries a `Max-Age`, bounded by the session's 7-day absolute cap) — this is what turns
 * it back into a session, on boot (`AuthProvider`) and on a 401 (`apiRequest`).
 *
 * Single-flight: a boot check and several 401s can land together, and a second rotation of the
 * same cookie would read as reuse to auth-service's theft check (the 30s grace window only softens
 * that, it is not something to lean on). Deliberately not built on `apiRequest`, so a refresh can
 * never itself trigger the 401-retry it serves.
 */

import { setAccessToken } from "./session";

/** `ok` — a new access token is in the session store. `unauthorized` — auth-service said no
 *  (401/403): the session is over. `error` — anything else; nothing was learned. */
export type RefreshOutcome = "ok" | "unauthorized" | "error";

let inFlight: Promise<RefreshOutcome> | null = null;

async function performRefresh(): Promise<RefreshOutcome> {
  let res: Response;
  try {
    res = await fetch("/api/v1/auth/operator/refresh", {
      method: "POST",
      // `_assert_not_cross_site`: every cookie-authenticated auth route requires it. No bearer
      // token — the cookie says who this is, and the call must work with no token held at all.
      headers: { Accept: "application/json", "X-Requested-With": "opsgrids" },
      credentials: "same-origin",
    });
  } catch {
    return "error";
  }
  if (res.status === 401 || res.status === 403) return "unauthorized";
  if (!res.ok) return "error";

  let token: unknown;
  try {
    token = ((await res.json()) as { access_token?: unknown } | null)?.access_token;
  } catch {
    return "error";
  }
  if (typeof token !== "string" || token === "") return "error";
  setAccessToken(token);
  return "ok";
}

/** Refresh the operator access token, sharing one request between concurrent callers. */
export function refreshOperatorToken(): Promise<RefreshOutcome> {
  inFlight ??= performRefresh().finally(() => {
    inFlight = null;
  });
  return inFlight;
}
