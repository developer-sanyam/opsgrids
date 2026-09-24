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

/** `"ok"` — a new access token is already stored. `"unauthorized"` — the refresh endpoint
 *  itself said no (401/403): the session is definitively over. `"error"` — anything else
 *  (network failure, an unexpected response): unknown, and not treated as a dead session. */
export type RefreshOutcome = "ok" | "unauthorized" | "error";

let inFlight: Promise<RefreshOutcome> | null = null;

/** Every legitimate caller of `/auth/refresh` sends this — see `services/auth`'s
 *  `_assert_not_cross_site`. A forged cross-site `<form>` POST cannot add a header. */
const CSRF_HEADER: Record<string, string> = { "X-Requested-With": "opsgrids" };

async function performRefresh(): Promise<RefreshOutcome> {
  const origin = getAuthOrigin();
  let res: Response;
  try {
    res = await fetch(`${origin ?? ""}/api/v1/auth/refresh`, {
      method: "POST",
      headers: { Accept: "application/json", ...CSRF_HEADER },
      credentials: origin ? "include" : "same-origin",
    });
  } catch {
    // A network failure says nothing about whether the session is still good.
    return "error";
  }

  if (res.status === 401 || res.status === 403) return "unauthorized";
  if (!res.ok) return "error";

  let body: unknown;
  try {
    const text = await res.text();
    body = text.trim() === "" ? null : JSON.parse(text);
  } catch {
    return "error";
  }
  const token =
    body !== null && typeof body === "object" && "access_token" in body
      ? (body as { access_token: unknown }).access_token
      : null;
  if (typeof token !== "string" || token === "") return "error";

  setAccessToken(token);
  return "ok";
}

/** Refresh the access token, deduplicating concurrent callers into one request. */
export async function refreshAccessToken(): Promise<RefreshOutcome> {
  if (!inFlight) {
    inFlight = performRefresh().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}
