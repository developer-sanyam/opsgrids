/**
 * The caller's own resolved grants — the first browser-visible statement of them.
 *
 * `GET /api/v1/members/me/grants` (account service) answers for the caller's own active
 * membership and nobody else's: there is no `?membership_id=`, by design, so the endpoint cannot
 * become a way to enumerate what other people can do. A suspended membership answers with an
 * empty list rather than a 403, which is the honest shape — the caller is a member, and holds
 * nothing.
 *
 * `pv` is the organization's permission version. The gateway invalidates a token whose `pv` has
 * fallen behind, so a role change produces a fresh token, and a fresh token is what makes
 * `PermissionProvider` ask again.
 *
 * Grants are strings in the ADR-0000 §5 grammar, `product.module.resource.action:SCOPE`, with the
 * widest scope already won — `scope.ts` parses them and never has to reconcile two entries for
 * the same code.
 */

import { apiRequest } from "../http";

export interface MyGrantsOut {
  membership_id: string;
  /** The organization's permission version, when the service reports one. */
  pv?: number;
  grants: string[];
}

export function fetchMyGrants(): Promise<MyGrantsOut> {
  return apiRequest<MyGrantsOut>("/members/me/grants");
}
