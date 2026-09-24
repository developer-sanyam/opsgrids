/**
 * The one place a silent refresh actually happens. Single-flight: a 401 retry, the proactive
 * timer and the boot-restore check can all land at once, and they share one in-flight request
 * rather than each burning their own rotation — `services/auth`'s reuse-family theft response
 * would otherwise see the second one as a stolen token (ADR-0003 addendum names the grace
 * window that also protects the *cross-tab* version of this same race).
 *
 * Deliberately **not** built on `apiRequest`: a refresh must never itself trigger the 401-retry
 * loop that function drives, or a session that is genuinely over would refresh forever. It also
 * never fires the global unauthorized handler on its own — the caller decides what a failed
 * refresh means for it (a proactive refresh failing is not the same situation as a 401 retry
 * failing), which is why this resolves to a three-way result rather than a boolean.
 */

import { getAuthOrigin } from "./origin";
import { setAccessToken } from "../session";

/**
 * `"ok"` carries `expiresIn` — seconds, relative to *this response*, exactly as the server
 * sent it — because scheduling the next proactive refresh from it (received-at + expiresIn,
 * both read off the client's own clock) is immune to client/server clock skew. Decoding the
 * token's own `exp` claim and comparing it to `Date.now()` is not: a client clock far enough
 * from the server's turns "refresh 60s before expiry" into "expiry already looks past", and
 * the proactive timer fires again immediately, forever (`AuthProvider`'s own defence is the
 * floor on top of this, for the one caller — a token already held at boot — that has no
 * `expiresIn` to start from and must fall back to decoding `exp`).
 *
 * `"unauthorized"` — the refresh endpoint itself said no (401/403): the session is
 * definitively over. `"error"` — anything else (network failure, a cross-origin call CORS
 * refused outright, an unexpected response): unknown, and not treated as a dead session.
 */
export type RefreshResult =
  | { outcome: "ok"; expiresIn: number }
  | { outcome: "unauthorized" }
  | { outcome: "error" };

let inFlight: Promise<RefreshResult> | null = null;

/** Every legitimate caller of `/auth/refresh` sends this — see `services/auth`'s
 *  `_assert_not_cross_site`. A forged cross-site `<form>` POST cannot add a header. */
const CSRF_HEADER: Record<string, string> = { "X-Requested-With": "opsgrids" };

async function performRefresh(): Promise<RefreshResult> {
  const origin = getAuthOrigin();
  let res: Response;
  try {
    res = await fetch(`${origin ?? ""}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { Accept: "application/json", ...CSRF_HEADER },
      credentials: origin ? "include" : "same-origin",
    });
  } catch {
    // A network failure (or a cross-origin call CORS refused before it ever reached the
    // server) says nothing about whether the session is still good.
    return { outcome: "error" };
  }

  if (res.status === 401 || res.status === 403) return { outcome: "unauthorized" };
  if (!res.ok) return { outcome: "error" };

  let body: unknown;
  try {
    const text = await res.text();
    body = text.trim() === "" ? null : JSON.parse(text);
  } catch {
    return { outcome: "error" };
  }
  const record = body !== null && typeof body === "object" ? (body as Record<string, unknown>) : null;
  const token = record && "access_token" in record ? record.access_token : null;
  const expiresIn = record && "expires_in" in record ? record.expires_in : null;
  if (typeof token !== "string" || token === "") return { outcome: "error" };
  if (typeof expiresIn !== "number" || !Number.isFinite(expiresIn) || expiresIn <= 0) {
    return { outcome: "error" };
  }

  setAccessToken(token);
  return { outcome: "ok", expiresIn };
}

/** Refresh the access token, deduplicating concurrent callers into one request. */
export async function refreshAccessToken(): Promise<RefreshResult> {
  if (!inFlight) {
    inFlight = performRefresh().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}
