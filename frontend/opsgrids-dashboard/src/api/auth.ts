/**
 * Auth-service calls the shared `@opsgrids/client` auth API does not expose: password change,
 * MFA enrolment, and switching organisation from an already-signed-in session. Sign-in itself —
 * including the second factor and the org-selection hop — goes through `useAuth()` in
 * `src/routes/Login.tsx`; nothing here is a stand-in for that any more.
 */
import { apiRequest, setAccessToken } from "@opsgrids/client";
import type { LoginResult } from "@opsgrids/client";

/**
 * Apply a completed sign-in outside the auth context — `switchOrganization` below is a bearer
 * call from an already-authenticated screen, which `AuthProvider`'s own `complete()` is not built
 * for — then hand off to a full reload so the provider's boot effect picks the freshly stored
 * token up and reloads the user and their grants for the newly chosen organisation. The token
 * itself is already safe to keep: `setAccessToken` mirrors it to `sessionStorage`, the same store
 * a same-tab reload already relies on.
 */
export function completeSessionAndReload(accessToken: string, destination: string): void {
  setAccessToken(accessToken);
  window.location.assign(destination);
}

// ------------------------------------------------------------------ account security

/** `POST /auth/mfa/enrol`'s answer: the factor to confirm, and what an authenticator app is set up from. */
export interface MfaEnrolStart {
  factor_id: string;
  secret: string;
  otpauth_uri: string;
}

/**
 * `POST /auth/mfa/enrol/confirm`'s answer. `recovery_codes` holds ten codes for the account's
 * **first** authenticator and is **empty** for one added beside an existing one — whose codes are
 * left as they were, so there is nothing to show.
 */
export interface MfaEnrolled {
  recovery_codes: string[];
}

/** What starting an enrolment has to prove: who is asking. */
export interface MfaEnrolInput {
  /** The member's current password — always required; a wrong one counts toward a lockout. */
  password: string;
  /**
   * A code from an authenticator already on the account (or one of its recovery codes) —
   * required exactly when the account has one. Left out of the request when empty: the server
   * answers a missing one with `mfa-current-code-required`, which is how a page that did not
   * know learns it is needed, whereas an empty string is a validation error of a different kind.
   */
  currentCode?: string | undefined;
}

/** `POST /auth/password/change` — self, requires the current password. */
export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiRequest<void>("/auth/password/change", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

/**
 * `POST /auth/mfa/enrol` — starts enrolment behind the password (and, when an authenticator is
 * already set up, a code from it); render `otpauth_uri` as a QR code. The answers a screen has to
 * tell apart are 422 `password-incorrect` (field `password`); 422 `mfa-current-code-required`,
 * `mfa-code-invalid` and `mfa-code-replayed` (field `current_code`); 403 `password-locked`,
 * `mfa-locked` and `mfa-unavailable-for-sso`; and 503 `mfa-reseal-pending` (ADR-0023).
 */
export function startMfaEnrolment({ password, currentCode }: MfaEnrolInput): Promise<MfaEnrolStart> {
  return apiRequest<MfaEnrolStart>("/auth/mfa/enrol", {
    method: "POST",
    body: { password, ...(currentCode ? { current_code: currentCode } : {}) },
  });
}

/**
 * `POST /auth/mfa/enrol/confirm` — proves the app is set up; recovery codes, when there are any,
 * are shown once. A wrong code is 422 `mfa-code-invalid` (field `code`); 404 means the enrolment
 * was begun by a different session, or has gone, and has to be started again.
 */
export function confirmMfaEnrolment(factorId: string, code: string): Promise<MfaEnrolled> {
  return apiRequest<MfaEnrolled>("/auth/mfa/enrol/confirm", {
    method: "POST",
    body: { factor_id: factorId, code },
  });
}

/**
 * `POST /auth/switch-org` — a bearer call; the org picker in the account menu uses this. Switching
 * to the organisation the token is already for is 409 `already-in-organization`.
 */
export function switchOrganization(organizationId: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/switch-org", {
    method: "POST",
    body: { organization_id: organizationId },
  });
}
