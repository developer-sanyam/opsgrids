/**
 * The employee link, from the HRMS employee page — account-service's member API, called with the
 * signed-in user's **own token** (ADR-0008 Q1). `/api/v1/members` is a platform route with no
 * product key on the gateway, so this needs no service token, no new internal surface and no new
 * trust relationship: the server's own `core.identity.membership.write` ORG floor is the control,
 * and the screen hides the field from anyone who lacks it as a hint, never as the enforcement.
 *
 * Deliberately **not** in `client.ts`: nothing here is an HRMS service, and a screen that mocks the
 * HRMS client should not have to mock the platform's directory as well.
 *
 * Four calls, no more — the link is `employee_id` on a membership, so:
 *
 * - `getMemberLinkedTo` — which member is this employee? (`?employee_id=`, at most one)
 * - `listUnlinkedMembers` — the picker (`?linked=false`, ADR-0008 §9)
 * - `linkMember` — `PATCH {employee_id}`
 * - `unlinkMember` — `PATCH {employee_id: null}`
 *
 * Every id in a path is server-issued and still `encodeURIComponent`-ed: a value that is not a UUID
 * must fail as a 404, never reshape the URL.
 */

import { apiRequest } from "@opsgrids/client";

import type { MemberLinkPatch, MemberOut, Paged } from "./types";

/** The picker's page size — the server's own cap is 200; a person picking one name never needs it. */
export const MEMBER_PICKER_SIZE = 20;

/**
 * The member linked to `employeeId`, or `null` when nobody is. The link is one-to-one within an
 * organization (a partial unique index on `(organization_id, employee_id)`), so a page of one is
 * all there is to read. Narrowed by the caller's scope on the server: someone who cannot see the
 * member gets `null`, which is indistinguishable from "unlinked" and must be read that way.
 */
export async function getMemberLinkedTo(employeeId: string): Promise<MemberOut | null> {
  const query = new URLSearchParams({ employee_id: employeeId, size: "1" });
  const page = await apiRequest<Paged<MemberOut>>(`/members?${query.toString()}`);
  return page.items[0] ?? null;
}

/** Members with no employee link — the ones whose approvals inbox is silently empty. */
export function listUnlinkedMembers(params: { q?: string; size?: number }): Promise<Paged<MemberOut>> {
  const query = new URLSearchParams({ linked: "false", size: String(params.size ?? MEMBER_PICKER_SIZE) });
  if (params.q) query.set("q", params.q);
  return apiRequest<Paged<MemberOut>>(`/members?${query.toString()}`);
}

function patchLink(membershipId: string, body: MemberLinkPatch): Promise<MemberOut> {
  return apiRequest<MemberOut>(`/members/${encodeURIComponent(membershipId)}`, {
    method: "PATCH",
    body,
  });
}

/**
 * Link a member to an employee. A **null → employee** change does not bump the organization's
 * permission version, so the member's next token carries it within one access-token lifetime
 * (ADR-0008 §7). Answers 409 when the employee is already linked to another member.
 */
export function linkMember(membershipId: string, employeeId: string): Promise<MemberOut> {
  return patchLink(membershipId, { employee_id: employeeId });
}

/**
 * Remove a member's employee link. This **does** bump the permission version — every access token
 * in the organization is refused at the gateway and refreshes (ADR-0008 §7) — which is why the
 * screen says so before it asks.
 */
export function unlinkMember(membershipId: string): Promise<MemberOut> {
  return patchLink(membershipId, { employee_id: null });
}
