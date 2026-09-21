/**
 * Auth-service calls the shared `@opsgrids/client` auth API does not expose yet
 * (`mfaVerify`, `switchOrg`, password change, MFA enrolment). Boundary note: this belongs in
 * `@opsgrids/client/auth/api.ts` next to `login`/`selectOrg` so every app gets it — this app may
 * not touch `opsgrids-client`, so it is built here instead, against the same `apiRequest`
 * `@opsgrids/client` itself uses. See the report for the exact change to make there.
 */
import { apiRequest, setAccessToken } from "@opsgrids/client";
import type { LoginResult } from "@opsgrids/client";

/** `POST /auth/mfa/verify` — public (pre-auth token), completes or asks for an organisation. */
export function mfaVerify(preAuthToken: string, code: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/mfa/verify", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, code },
    auth: false,
    signalUnauthorized: false,
  });
}

/**
 * `POST /auth/select-org`, called directly (not through `useAuth().selectOrg`) because the
 * pre-auth token here is the one `mfaVerify` minted, not the one the auth context is holding
 * from the original `login()` call.
 */
export function selectOrgWithToken(preAuthToken: string, organizationId: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/select-org", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, organization_id: organizationId },
    auth: false,
    signalUnauthorized: false,
  });
}

/**
 * Apply a completed sign-in outside the auth context (the MFA branch cannot reach
 * `AuthProvider`'s private `complete()`), then hand off to a full reload so `AuthProvider`'s own
 * boot effect picks the freshly stored token up and loads the user through the one path that
 * does that. The token itself is already safe to keep: `setAccessToken` mirrors it to
 * `sessionStorage`, the same store a same-tab reload already relies on.
 */
export function completeSessionAndReload(accessToken: string, destination: string): void {
  setAccessToken(accessToken);
  window.location.assign(destination);
}

// ------------------------------------------------------------------ account security

export interface MfaEnrolStart {
  factor_id: string;
  secret: string;
  otpauth_uri: string;
}

export interface MfaEnrolled {
  recovery_codes: string[];
}

/** `POST /auth/password/change` — self, requires the current password. */
export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return apiRequest<void>("/auth/password/change", {
    method: "POST",
    body: { current_password: currentPassword, new_password: newPassword },
  });
}

/** `POST /auth/mfa/enrol` — starts enrolment; render `otpauth_uri` as a QR code. */
export function startMfaEnrolment(): Promise<MfaEnrolStart> {
  return apiRequest<MfaEnrolStart>("/auth/mfa/enrol", { method: "POST" });
}

/** `POST /auth/mfa/enrol/confirm` — proves the app is set up; recovery codes are shown once. */
export function confirmMfaEnrolment(factorId: string, code: string): Promise<MfaEnrolled> {
  return apiRequest<MfaEnrolled>("/auth/mfa/enrol/confirm", {
    method: "POST",
    body: { factor_id: factorId, code },
  });
}

/** `POST /auth/switch-org` — a bearer call; the org picker in the account menu uses this. */
export function switchOrganization(organizationId: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/switch-org", {
    method: "POST",
    body: { organization_id: organizationId },
  });
}
