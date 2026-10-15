/**
 * The employee link on a login -- `account-service`'s member API, called with the signed-in user's
 * own token (ADR-0008 Q1). Account is the **only** writer of `memberships.employee_id`; this
 * product never sends a membership id to employee-service. The write is `PATCH /members/{id}` and
 * needs `core.identity.membership.write` at ORG, which the server enforces; the control that calls
 * it is hidden from everyone else, but hiding is not the protection.
 */

import { apiRequest, buildQuery as qs } from "@opsgrids/client";

import { USE_FIXTURES } from "./client";
import type { Paged } from "./types";

/** The parts of account's `MemberOut` this product reads. */
export interface MemberOut {
  id: string;
  user_id: string;
  email: string;
  full_name: string;
  display_name: string | null;
  status: string;
  is_owner: boolean;
  employee_id: string | null;
}

/** A member's name as a person reads it. */
export function memberName(member: Pick<MemberOut, "display_name" | "full_name">): string {
  return member.display_name?.trim() || member.full_name;
}

/** `GET /members?linked=false` -- the logins with no employee record, which is the picker. Active
 *  members only, one page of up to 100: an organization with more unlinked logins than that is
 *  filtered by search, not paged through a dropdown. */
export async function listUnlinkedMembers(q?: string): Promise<MemberOut[]> {
  if (USE_FIXTURES) return [];
  const query = qs({ linked: "false", status: "active", size: 100, q: q?.trim() || undefined });
  const page = await apiRequest<Paged<MemberOut>>(`/members${query}`);
  return page.items;
}

/** `GET /members?employee_id=` -- the login linked to this employee, or `null`. */
export async function getMemberLinkedTo(employeeId: string): Promise<MemberOut | null> {
  if (USE_FIXTURES) return null;
  const query = qs({ employee_id: employeeId, size: 1 });
  const page = await apiRequest<Paged<MemberOut>>(`/members${query}`);
  return page.items[0] ?? null;
}

/**
 * `PATCH /members/{id}` with `{employee_id}`: link the login to an employee record, or unlink it
 * with `null` (an explicit null unlinks; an absent key would leave it alone). A link already held
 * by another login is a 409 with a plain sentence. Linking a login for the first time takes effect
 * at its next sign-in refresh; unlinking or changing one refreshes everyone's sign-in in the
 * organization (ADR-0008 §7).
 */
export async function setMemberEmployee(
  membershipId: string,
  employeeId: string | null,
): Promise<MemberOut> {
  if (USE_FIXTURES) throw new Error("Linking a login is not available in fixture mode.");
  return apiRequest<MemberOut>(`/members/${encodeURIComponent(membershipId)}`, {
    method: "PATCH",
    body: { employee_id: employeeId },
  });
}
