/**
 * The signed-in caller, shaped for AppShell v2's account card — nothing this hook returns is an
 * authorization decision, only display.
 */

import { useMemo } from "react";

import { useAuth } from "../auth/context";

export interface CurrentUser {
  name: string;
  email: string;
  /** Up to two letters for an avatar — first name's initial plus last name's, when there is one. */
  initials: string;
  /**
   * The caller's role in the active organization, joined for display when they hold more than
   * one. `null` when `MeOut` carries no role for the active organization (`roles: []`, or no
   * matching entry in `organizations` — a role *code*, since `MeOut` does not carry a friendlier
   * label; see the report for this gap).
   */
  roleDisplay: string | null;
}

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "";
  const first = parts[0]?.[0] ?? "";
  if (parts.length === 1) return first.toUpperCase();
  const last = parts[parts.length - 1]?.[0] ?? "";
  return `${first}${last}`.toUpperCase();
}

/** `null` before sign-in completes — the same moment `useAuth().user` is `null`. */
export function useCurrentUser(): CurrentUser | null {
  const { user } = useAuth();

  return useMemo(() => {
    if (!user) return null;
    const activeOrg = user.organizations.find((org) => org.organization_id === user.organization_id) ?? null;
    const roles = activeOrg ? activeOrg.roles : user.roles;
    return {
      name: user.full_name,
      email: user.email,
      initials: initialsOf(user.full_name),
      roleDisplay: roles.length > 0 ? roles.join(", ") : null,
    };
  }, [user]);
}
