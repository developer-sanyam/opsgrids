/**
 * Thin, typed calls to auth-service's **staff** endpoints through admin-gateway.
 *
 * These are the real, shipped paths (`services/auth/app/api/routes.py`): operator sign-in lives
 * under `/api/v1/auth/operator/*`, the same prefix both gateways already carry for
 * authentication — there is no separate `/admin/auth/*` router. Every pre-auth step (login, the
 * two enrolment calls, verify) carries its credential in the body rather than a bearer token, and
 * a 401 from one means "wrong credential", not "stale session", so none of them trips the global
 * sign-out handler.
 */

import { apiRequest } from "@/api/http";
import type {
  MfaEnrolStart,
  OperatorLoginResult,
  OperatorMe,
  OperatorMfaEnrolled,
  OperatorTokenOut,
} from "./types";

/** No bearer token exists yet for any of these — the credential is the body — and a 401 here means
 *  the credential was wrong, not that a session went stale. */
const PRE_AUTH = { auth: false, signalUnauthorized: false } as const;

/** `POST /auth/operator/login`. Never a token by itself — only which second-factor step, `mfa` or
 *  `enrol`, comes next (ADR-0003 addendum: staff MFA is mandatory). */
export function login(email: string, password: string): Promise<OperatorLoginResult> {
  return apiRequest<OperatorLoginResult>("/auth/operator/login", {
    method: "POST",
    body: { email, password },
    ...PRE_AUTH,
  });
}

/**
 * `POST /auth/operator/mfa/enrol`. Starts an operator's first factor. A password is not enough to
 * reach this: the out-of-band `enrollment_code` an owner issued must redeem too, and it is
 * single-use — a typo spends it.
 */
export function beginEnrolment(preAuthToken: string, enrollmentCode: string): Promise<MfaEnrolStart> {
  return apiRequest<MfaEnrolStart>("/auth/operator/mfa/enrol", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, enrollment_code: enrollmentCode },
    ...PRE_AUTH,
  });
}

/**
 * `POST /auth/operator/mfa/enrol/confirm`. Proves the authenticator works, and completes the
 * sign-in that started enrolment: the answer is a token **and** the recovery codes, shown once.
 */
export function confirmEnrolment(
  preAuthToken: string,
  factorId: string,
  code: string,
): Promise<OperatorMfaEnrolled> {
  return apiRequest<OperatorMfaEnrolled>("/auth/operator/mfa/enrol/confirm", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, factor_id: factorId, code },
    ...PRE_AUTH,
  });
}

/**
 * `POST /auth/operator/mfa/verify`. Six digits from the authenticator, or a recovery code. A wrong
 * code is a 401 that counts toward the lockout; a confirmed factor this deployment cannot yet read
 * is a 503 that deliberately does not (`services.MfaResealPendingProblem`) — see `./problems.ts`.
 */
export function verifyMfa(preAuthToken: string, code: string): Promise<OperatorTokenOut> {
  return apiRequest<OperatorTokenOut>("/auth/operator/mfa/verify", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, code },
    ...PRE_AUTH,
  });
}

/** `GET /auth/operator/me`. Who the current staff token belongs to, and their operator-roles. */
export function me(): Promise<OperatorMe> {
  return apiRequest<OperatorMe>("/auth/operator/me");
}

/**
 * `POST /auth/operator/logout`. Best-effort — clears the refresh cookie server-side.
 *
 * `auth: false`, and the CSRF header every cookie-authenticated auth route now requires
 * (`services/auth`'s `_assert_not_cross_site`, security review fix round 1, MINOR 1) — this is
 * cookie-authenticated same as the tenant logout, and does not need a bearer token to say who is
 * signing out.
 */
export function logout(): Promise<void> {
  return apiRequest<void>("/auth/operator/logout", {
    method: "POST",
    auth: false,
    signalUnauthorized: false,
    headers: { "X-Requested-With": "opsgrids" },
  });
}
