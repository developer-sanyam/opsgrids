/**
 * Operator auth wire types — a field-for-field mirror of the operator half of
 * `libs/contracts/openapi/auth.json` (`services/auth/app/schemas.py`), which
 * `auth/contract.test.ts` holds them to.
 *
 * Staff live in their own table, entirely apart from the tenant user directory, and authenticate
 * to the **operator** audience. MFA is mandatory for them (ADR-0003 addendum): a password alone
 * never earns a token, only a pre-auth token naming which second-factor step comes next.
 */

/** The three operator roles (ADR-0003 addendum). `owner` is every route accepting it. */
export type OperatorRole = "owner" | "billing_ops" | "support";

/** `OperatorMfaRequired` — the account has a confirmed factor and must present it. */
export interface OperatorMfaRequired {
  status: "mfa_required";
  pre_auth_token: string;
}

/**
 * `OperatorMfaEnrollmentRequired` — the account has no confirmed factor. There is no path but
 * enrolling one, and it needs an out-of-band enrolment code as well as this token.
 */
export interface OperatorMfaEnrollmentRequired {
  status: "mfa_enrollment_required";
  pre_auth_token: string;
}

/** `POST /auth/operator/login` never answers with a token — only which step comes next. */
export type OperatorLoginResult = OperatorMfaRequired | OperatorMfaEnrollmentRequired;

/** `MfaEnrolStart` — the secret to scan or type into an authenticator app. */
export interface MfaEnrolStart {
  factor_id: string;
  secret: string;
  /** Render as a QR code; `secret` is the fallback for manual entry. */
  otpauth_uri: string;
}

/** `OperatorTokenOut` — a completed operator sign-in. The refresh token is an httpOnly cookie. */
export interface OperatorTokenOut {
  status: "complete";
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  operator_roles: string[];
}

/**
 * `OperatorMfaEnrolled` — confirming enrolment completes the sign-in that started it, and returns
 * the recovery codes **once**: they are stored hashed and never retrievable again.
 */
export interface OperatorMfaEnrolled extends OperatorTokenOut {
  recovery_codes: string[];
}

/** `OperatorMeOut` — who the current staff token belongs to, and what they may operate. */
export interface OperatorMe {
  staff_id: string;
  email: string;
  display_name: string;
  operator_roles: string[];
  amr: string[];
}

/** What the sign-in screen renders next, as `login` reports it. */
export type OperatorStep = "mfa" | "enrol";
