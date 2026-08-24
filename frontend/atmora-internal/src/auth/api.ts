/**
 * Thin, typed calls to the auth service's **staff** endpoints through admin-gateway. Login carries
 * a pre-auth credential in the body rather than a bearer token, and a 401 from it means "wrong
 * credentials" — not "stale session" — so it never trips the global sign-out handler.
 *
 * These paths are the documented target (ADR-0004 P6a: `POST /api/v1/admin/auth/login`, `.../me`);
 * in fixtures mode they are never reached — `AuthProvider` short-circuits to a stand-in operator.
 */

import { apiRequest } from "@/api/http";
import type { OperatorLoginResult, OperatorMe } from "./types";

/** `POST /admin/auth/login`. Returns a discriminated result on `status`. */
export function login(email: string, password: string): Promise<OperatorLoginResult> {
  return apiRequest<OperatorLoginResult>("/admin/auth/login", {
    method: "POST",
    body: { email, password },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `GET /admin/auth/me`. Who the current staff token belongs to, and their operator-roles. */
export function me(): Promise<OperatorMe> {
  return apiRequest<OperatorMe>("/admin/auth/me");
}

/** `POST /admin/auth/logout`. Best-effort — clears the refresh cookie server-side. */
export function logout(): Promise<void> {
  return apiRequest<void>("/admin/auth/logout", { method: "POST", signalUnauthorized: false });
}
