/**
 * Thin, typed calls to the auth service through the gateway. Login and select-org carry a
 * pre-auth credential in the body rather than a bearer token, and a 401 from them means "wrong
 * credentials" — not "stale session" — so they never trip the global sign-out handler.
 */

import { apiRequest } from "../http";
import { getAuthOrigin } from "./origin";
import type { LoginResult, MeOut } from "./types";

/** `POST /auth/login`. Returns a discriminated result on `status`. */
export function login(email: string, password: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/login", {
    method: "POST",
    body: { email, password },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/select-org`. Exchanges a pre-auth token + chosen org for an access token. */
export function selectOrg(preAuthToken: string, organizationId: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/select-org", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, organization_id: organizationId },
    auth: false,
    signalUnauthorized: false,
  });
}

/**
 * `POST /auth/mfa/verify`. The second step of sign-in: the pre-auth token `login` answered with,
 * plus six digits from an authenticator app or a recovery code.
 *
 * Answers the same union `login` does — `complete`, or `org_selection_required` **with a fresh
 * pre-auth token** for a user who belongs to several organizations. A wrong code is a 401 carrying
 * the server's own sentence ("That code is not valid"); the pre-auth token survives it, so the
 * screen keeps the field and lets the user try again. Like `login`, it never trips the global
 * sign-out handler: there is no session here to be stale.
 */
export function mfaVerify(
  preAuthToken: string,
  code: string,
  organization?: string,
): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/mfa/verify", {
    method: "POST",
    body: {
      pre_auth_token: preAuthToken,
      code,
      ...(organization !== undefined ? { organization } : {}),
    },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `GET /auth/me`. Who the current token belongs to, plus their org memberships. */
export function me(): Promise<MeOut> {
  return apiRequest<MeOut>("/auth/me");
}

/**
 * `POST /auth/logout`. Best-effort — clears the refresh cookie server-side.
 *
 * Goes to the auth origin (`configureAuthOrigin`), same as `auth/refresh.ts` — same-origin by
 * default, cross-origin to `app.opsgrids.com` for every product SPA that centralises its
 * refresh there (ADR-0003 addendum). Ending the session on the host that actually holds the
 * cookie is what makes every other open tab and app fail its next refresh and land back on
 * `/login`; logging out only against this app's own (same-)origin would leave that cookie
 * live everywhere else.
 */
export function logout(): Promise<void> {
  const origin = getAuthOrigin();
  return apiRequest<void>("/auth/logout", {
    method: "POST",
    signalUnauthorized: false,
    headers: { "X-Requested-With": "opsgrids" },
    ...(origin ? { baseUrl: origin, credentials: "include" } : {}),
  });
}
