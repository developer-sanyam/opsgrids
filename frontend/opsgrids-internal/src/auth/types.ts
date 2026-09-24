/**
 * Operator auth wire types. Backed by a real, shipped endpoint now (`services/auth/app/schemas.py`
 * — `OperatorTokenOut`, `OperatorMfaRequired`, `OperatorMfaEnrollmentRequired`, `OperatorMeOut`): a
 * dedicated staff table, entirely separate from the tenant user directory, whose accounts
 * authenticate to the **operator** audience and receive an `stf` claim plus their operator-roles.
 *
 * **Not yet reconciled field-for-field with the live schemas** — that is the follow-up slice that
 * wires this app onto `opsgrids-client` and builds the MFA enrolment/verify screens the backend
 * now requires unconditionally (ADR-0003 addendum: a staff account with no confirmed factor can
 * reach only enrolment, never a token). `OperatorRole` itself is reconciled (the live roles really
 * are `owner` / `billing_ops` / `support`, confirmed against `services/console/app/api/routes.py`'s
 * `require_operator(roles={...})` guards); what's left is that the identity field is `staff_id`
 * (not `operator_id`), and login can resolve to `mfa_enrollment_required` in addition to
 * `mfa_required` — neither of which is represented here yet.
 */

/** One operator-role a staff account holds. `owner` holds everything; `billing_ops` and `support`
 *  are narrower (ADR-0003 addendum's role matrix). */
export type OperatorRole = "owner" | "billing_ops" | "support";

/** `OperatorTokenOut` — a completed staff sign-in. The refresh token is an httpOnly cookie. */
export interface OperatorLoginComplete {
  status: "complete";
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  operator_id: string;
}

/** `OperatorMfaRequired` — a second factor is needed. Staff MFA enrolment/verify screens are the
 *  follow-up slice; see the module docstring. */
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
