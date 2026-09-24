/**
 * Thin, typed calls to auth-service's **staff** endpoints through admin-gateway.
 *
 * These are the shipped paths (`services/auth/app/api/routes.py`): operator sign-in lives under
 * `/api/v1/auth/operator/*`, the prefix both gateways already carry for authentication — there
 * is no `/admin/auth/*` router. Every sign-in step carries its pre-auth credential in the body
 * rather than a bearer token, and a 401 from one means "wrong credential", not "stale session", so
 * none of them trips the global sign-out handler. Requests go through `operatorRequest`, which is
 * what keeps a tenant token out of these calls.
 */

import { operatorRequest } from "./operatorRequest";
import type {
  MfaEnrolStart,
  OperatorLoginResult,
  OperatorMe,
  OperatorMfaEnrolled,
  OperatorTokenOut,
} from "./types";

const PRE_AUTH = { auth: false, signalUnauthorized: false } as const;

/** `POST /auth/operator/login`. Never a token — only which second-factor step comes next. */
export function login(email: string, password: string): Promise<OperatorLoginResult> {
  return operatorRequest<OperatorLoginResult>("/auth/operator/login", {
    method: "POST",
    body: { email, password },
    ...PRE_AUTH,
  });
}

/**
 * `POST /auth/operator/mfa/enrol`. Starts an operator's first factor. A password is not enough to
 * reach this: the out-of-band `enrollment_code` an owner issued must redeem too — and it is
 * single-use, so a typo spends it.
 */
export function beginEnrolment(preAuthToken: string, enrollmentCode: string): Promise<MfaEnrolStart> {
  return operatorRequest<MfaEnrolStart>("/auth/operator/mfa/enrol", {
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
  return operatorRequest<OperatorMfaEnrolled>("/auth/operator/mfa/enrol/confirm", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, factor_id: factorId, code },
    ...PRE_AUTH,
  });
}

/**
 * `POST /auth/operator/mfa/verify`. Six digits from the authenticator, or a recovery code. A wrong
 * code is a 401 that counts toward the lockout; a factor this deployment cannot yet read is a 503
 * that does not (see `mfaProblem`).
 */
export function verifyMfa(preAuthToken: string, code: string): Promise<OperatorTokenOut> {
  return operatorRequest<OperatorTokenOut>("/auth/operator/mfa/verify", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, code },
    ...PRE_AUTH,
  });
}

/** `GET /auth/operator/me`. Who the current staff token belongs to, and their operator roles. */
export function me(): Promise<OperatorMe> {
  return operatorRequest<OperatorMe>("/auth/operator/me");
}

/**
 * `POST /auth/operator/logout`. Best-effort — ends the session by the httpOnly refresh cookie, so
 * it needs no bearer token and works even when the access token has already lapsed.
 */
export function logout(): Promise<void> {
  return operatorRequest<void>("/auth/operator/logout", { method: "POST", ...PRE_AUTH });
}
