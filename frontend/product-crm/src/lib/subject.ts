/**
 * "Which rep record is mine" — the fact the Overview's "My pipeline" toggle needs to narrow
 * `getAllDeals()` and `listLeads()` to the caller's own rows, the same way `/deals/summary`'s own
 * `owner=me` resolves it server-side from the token.
 *
 * Read here, unverified, the same "hint for the interface, never an authorization decision" rule
 * `decodeAccessToken` itself documents (`product-hrms/src/lib/subject.ts` does the same for its
 * own "My attendance" surfaces). The server re-checks every owner-scoped read regardless: an
 * OWN-scoped caller who names someone else's id gets nothing back, never that colleague's deals.
 */
import { decodeAccessToken } from "@opsgrids/client";

import { OWNERS } from "@/api/fixtures";
import { USE_FIXTURES } from "@/api/client";

/** The signed-in rep's own id, or `null` when the token carries none — a membership with no CRM
 *  rep record toggles nothing. Fixture mode mints no real token, so it reports the fixture
 *  identity's own rep record (Sofia Marchetti, `AuthProvider.tsx`'s `FIXTURE_USER`) instead. */
export function getMySubjectRef(): string | null {
  if (USE_FIXTURES) return OWNERS[0]?.id ?? null;
  return decodeAccessToken()?.sref ?? null;
}
