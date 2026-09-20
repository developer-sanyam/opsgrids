/**
 * "Which employee record is mine" — the fact every own-record gate in this slice needs (§4.C's
 * "Request a correction" button, §4.B's "you can't decide your own request" line).
 *
 * The screens spec asked for it as `subject_ref` on `MeOut` (C5); what the platform actually
 * grants it as is the access token's `sref` claim (`services/auth/app/tokens.py`,
 * `AccessClaims.sref`) — read here, unverified, the same "hint for the interface, never an
 * authorization decision" rule `decodeAccessToken` itself documents. The server re-checks every
 * own-record action regardless (ADR-0003 §3).
 */

import { decodeAccessToken } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { FIXTURE_EMPLOYEE_ID } from "@/api/fixtures";

/**
 * The signed-in employee's own id, or `null` when the caller has no employee record (a
 * membership with no employee link decides and requests nothing — ADR-0006 §6 rule 2). Fixture
 * mode mints no real token, so it reports the fixture identity's employee id instead.
 */
export function getMyEmployeeId(): string | null {
  if (USE_FIXTURES) return FIXTURE_EMPLOYEE_ID;
  return decodeAccessToken()?.sref ?? null;
}
