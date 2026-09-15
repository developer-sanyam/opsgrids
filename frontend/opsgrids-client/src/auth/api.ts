/**
 * Thin, typed calls to the auth service through the gateway. Login and select-org carry a
 * pre-auth credential in the body rather than a bearer token, and a 401 from them means "wrong
 * credentials" — not "stale session" — so they never trip the global sign-out handler.
 */

import { apiRequest } from "../http";
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

/** `GET /auth/me`. Who the current token belongs to, plus their org memberships. */
export function me(): Promise<MeOut> {
  return apiRequest<MeOut>("/auth/me");
}

/** `POST /auth/logout`. Best-effort — clears the refresh cookie server-side. */
export function logout(): Promise<void> {
  return apiRequest<void>("/auth/logout", { method: "POST", signalUnauthorized: false });
}
