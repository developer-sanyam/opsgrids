/**
 * What each operator role may reach — the ADR-0003 addendum's role matrix, as the console renders
 * it.
 *
 * **This hides UI. It is not enforcement.** Every `staff_only` route declares its roles with
 * `require_operator(roles={…})` and refuses the rest with a plain 403; a hidden link is a `curl`
 * away. The console reads the same table so an operator is not offered a screen the server will
 * refuse, and renders the 403 it may get anyway.
 *
 * `owner` is not "every permission as a stored list" — it is every `require_operator` call site
 * accepting it, which is why every row below names it.
 */

import type { OperatorRole } from "./types";

/** A thing the console shows, named by what it reads or writes. */
export type Capability =
  | "organizations"
  | "metrics"
  | "audit"
  | "subscriptions"
  | "usage"
  | "overrides"
  | "creditLedger"
  | "catalog";

const MATRIX: Record<Capability, readonly OperatorRole[]> = {
  // `/admin/organizations` and its detail: a support ticket starts with "what does this account
  // look like" before anyone knows whether it is a billing or a catalog question.
  organizations: ["owner", "billing_ops", "support"],
  // Financial reads and the audit trail do not admit every role.
  metrics: ["owner", "billing_ops"],
  audit: ["owner", "support"],
  subscriptions: ["owner", "billing_ops"],
  usage: ["owner", "billing_ops"],
  overrides: ["owner", "billing_ops"],
  creditLedger: ["owner", "billing_ops"],
  // The Plans screen. Catalog *writes* are owner-only on the server, and there is no staff catalog
  // *read* route yet — the addendum's table has no row for it, and one belongs there before it
  // ships. Until then this admits the roles that run pricing overrides, which is who needs to see
  // a plan to price against it.
  catalog: ["owner", "billing_ops"],
};

export const ROLE_LABEL: Record<OperatorRole, string> = {
  owner: "Owner",
  billing_ops: "Billing ops",
  support: "Support",
};

/** The roles a capability admits, for a permission-denied screen that names them. */
export function rolesFor(capability: Capability): readonly OperatorRole[] {
  return MATRIX[capability];
}

/** Whether any of the operator's roles admits the capability. Unknown role strings admit nothing. */
export function canDo(roles: readonly string[], capability: Capability): boolean {
  return MATRIX[capability].some((role) => roles.includes(role));
}

/** "Owner · Support" — the roles as a person reads them, in the matrix's own order. */
export function roleText(roles: readonly string[]): string {
  const known = (Object.keys(ROLE_LABEL) as OperatorRole[])
    .filter((role) => roles.includes(role))
    .map((role) => ROLE_LABEL[role]);
  const unknown = roles.filter((role) => !(role in ROLE_LABEL));
  return [...known, ...unknown].join(" · ") || "Operator";
}
