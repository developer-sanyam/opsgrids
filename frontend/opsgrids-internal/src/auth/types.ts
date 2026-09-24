/**
 * Operator auth wire types — a field-for-field mirror of the operator half of
 * `libs/contracts/openapi/auth.json` (`services/auth/app/schemas.py`).
 *
 * Staff live in their own table, entirely apart from the tenant user directory, and authenticate
 * to the **operator** audience. MFA is mandatory for them (ADR-0003 addendum): `POST
 * /auth/operator/login` never returns a token from a password alone, only a pre-auth token naming
 * which second-factor step comes next — `mfa_required` for an account with a confirmed
 * authenticator, `mfa_enrollment_required` for one with none.
 */

/** The three operator roles (ADR-0003 addendum's role matrix), confirmed against
 *  `services/console/app/api/routes.py`'s `require_operator(roles={...})` guards. */
export type OperatorRole = "owner" | "billing_ops" | "support";

/** `OperatorMfaRequired` — a staff account with an already-confirmed factor must present it. */
export interface OperatorMfaRequired {
  status: "mfa_required";
  pre_auth_token: string;
}

/**
 * `OperatorMfaEnrollmentRequired` — a staff account with no confirmed factor. There is no path but
 * enrolling one: unlike the tenant flow, an operator cannot land here and simply keep going.
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

/** `OperatorTokenOut` — a completed operator sign-in. The refresh token is set as an httpOnly
 *  cookie the JavaScript never sees; this body never carries one. */
export interface OperatorTokenOut {
  status: "complete";
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  operator_roles: string[];
}

/**
 * `OperatorMfaEnrolled` — confirming enrolment completes the sign-in that started it (the freshly
 * proven code already is the second factor; re-presenting it through `mfa/verify` would only doubt
 * a check that just passed), and returns the recovery codes **once**: stored hashed, never
 * retrievable again.
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

/** What an authenticator app needs to enrol: the URI for a QR code, and the secret to type. The
 *  factor id behind it stays in the provider — the screen never needs to name it. */
export interface EnrolmentSetup {
  secret: string;
  otpauthUri: string;
}
