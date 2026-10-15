/**
 * "Which sales rep am I" -- the rep record that records made by this login belong to.
 *
 * A login becomes a rep in one of three ways, and this is the one place that knows all of them:
 *
 * 1. its employee record is its roster id (`sref`) -- an HRMS tenant, or the demo seed;
 * 2. its **membership id** is its roster id -- what `PUT /customers/owners/me` creates, and what
 *    the deal service defaults an owner to for a login with no employee record, such as the
 *    self-serve organization owner;
 * 3. a roster row already carries the login's own email -- the rep the old one-click "make me the
 *    owner" made with a random id. A *display and defaulting* convenience only: ownership scope
 *    still compares `sref` on the server, never this match.
 *
 * Read here, unverified, like every other `decodeAccessToken` hint: the server re-checks.
 */
import { decodeAccessToken } from "@opsgrids/client";

import type { Owner } from "@/api/types";

export interface RepIdentity {
  email: string;
  full_name: string;
  membership_id: string | null;
}

/** The roster row that is this login, or `undefined` when the login is not on the roster yet.
 *  An inactive row still matches: the caller decides whether that is usable. */
export function findMyRep(
  owners: readonly Owner[],
  me: Pick<RepIdentity, "email" | "membership_id"> | null,
  sref: string | null = decodeAccessToken()?.sref ?? null,
): Owner | undefined {
  if (sref !== null) {
    const byEmployee = owners.find((o) => o.id === sref);
    if (byEmployee !== undefined) return byEmployee;
  }
  if (me === null) return undefined;
  if (me.membership_id !== null) {
    const byMembership = owners.find((o) => o.id === me.membership_id);
    if (byMembership !== undefined) return byMembership;
  }
  const email = me.email.trim().toLowerCase();
  return email === "" ? undefined : owners.find((o) => o.email.trim().toLowerCase() === email);
}

/** What `PUT /customers/owners/me` is asked with: the login's own name and email, or `null` when
 *  the session has no usable name (the service requires one). */
export function repSeed(me: RepIdentity | null): { full_name: string; email: string } | null {
  if (me === null) return null;
  const full_name = me.full_name.trim();
  return full_name === "" || me.email.trim() === "" ? null : { full_name, email: me.email.trim() };
}
