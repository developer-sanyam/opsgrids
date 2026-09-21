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
