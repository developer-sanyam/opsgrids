/**
 * Thin, typed calls to auth-service's **staff** endpoints through admin-gateway. Login carries
 * a pre-auth credential in the body rather than a bearer token, and a 401 from it means "wrong
 * credentials" — not "stale session" — so it never trips the global sign-out handler.
 *
 * These are the **real, shipped** paths (`services/auth/app/api/routes.py`): operator sign-in
 * lives under `/api/v1/auth/operator/*`, the same prefix both gateways already carry for
 * authentication, never a separate `/admin/auth/*` surface — there is no such router. Staff MFA
 * is mandatory (ADR-0003 addendum): `login()` never resolves to a usable session by itself, only
 * to `mfa_required` or `mfa_enrollment_required`, and completing either of those is the next
 * slice's work — wiring this console onto `opsgrids-client` and building the enrolment/verify
 * screens. In fixtures mode none of this is reached — `AuthProvider` short-circuits to a
 * stand-in operator.
 */

import { apiRequest } from "@/api/http";
import type { OperatorLoginResult, OperatorMe } from "./types";

/** `POST /auth/operator/login`. Returns a discriminated result on `status` — never `complete`
 *  by itself; see the module docstring. */
export function login(email: string, password: string): Promise<OperatorLoginResult> {
  return apiRequest<OperatorLoginResult>("/auth/operator/login", {
    method: "POST",
    body: { email, password },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `GET /auth/operator/me`. Who the current staff token belongs to, and their operator-roles. */
export function me(): Promise<OperatorMe> {
  return apiRequest<OperatorMe>("/auth/operator/me");
}

/** `POST /auth/operator/logout`. Best-effort — clears the refresh cookie server-side. */
export function logout(): Promise<void> {
  return apiRequest<void>("/auth/operator/logout", { method: "POST", signalUnauthorized: false });
}
