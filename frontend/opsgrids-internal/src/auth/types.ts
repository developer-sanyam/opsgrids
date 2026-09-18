/**
 * Operator auth wire types. These will mirror `services/auth/app/schemas.py` for the **staff**
 * registry (ADR-0004 P6a): a dedicated staff table, entirely separate from the tenant user
 * directory, whose accounts authenticate to the **operator** audience and receive an `stf` claim
 * plus their operator-roles. Kept minimal until that endpoint lands.
 */

/** One operator-role a staff account holds (e.g. revenue-ops, support, billing-admin). */
export type OperatorRole =
  | "revenue_ops"
  | "support"
  | "billing_admin"
  | "read_only";

/** `OperatorTokenOut` — a completed staff sign-in. The refresh token is an httpOnly cookie. */
export interface OperatorLoginComplete {
  status: "complete";
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  operator_id: string;
}

/** `MfaRequired` — a second factor is needed (staff MFA is out of this slice). */
export interface OperatorLoginMfaRequired {
  status: "mfa_required";
  pre_auth_token: string;
}

export type OperatorLoginResult = OperatorLoginComplete | OperatorLoginMfaRequired;

/** `OperatorMeOut` — who the current staff token belongs to, and what they may operate. */
export interface OperatorMe {
  operator_id: string;
  email: string;
  full_name: string;
  /** The staff-side roles that gate operator actions (suspend, grant, migrate). */
  operator_roles: OperatorRole[];
  amr: string[];
  mfa_enabled: boolean;
}

/** What `login()` reports back to the screen. */
export type LoginStep = { kind: "complete" } | { kind: "mfa_required" };
