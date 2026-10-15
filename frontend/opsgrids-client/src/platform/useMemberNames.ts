/**
 * Turn the user id a record carries (`created_by`, `transitioned_by` — the principal's subject
 * id) into the name a person would recognise. Display only; no authorization rides on it.
 *
 * Names come from the organization's member directory (`GET /members`), which the caller may
 * read only with `core.identity.membership.read`. Without it, or while it loads, or when the
 * lookup fails, the caller's own name is still resolved and everyone else reads as "a team
 * member" — an id is never shown.
 */

import { useCallback, useMemo } from "react";

import { useAuth } from "../auth/context";
import { apiRequest } from "../http";
import { useCan } from "../permissions/context";
import { useAsync } from "../useAsync";

export const UNKNOWN_MEMBER_NAME = "a team member";

interface MemberRow {
  user_id: string;
  full_name: string;
  display_name: string | null;
}

interface MemberPage {
  items: MemberRow[];
}

/** The backend's page-size ceiling (`platform_core/pagination.py`). */
const DIRECTORY_PAGE_SIZE = 200;

export function useMemberNames(): (userId: string | null | undefined) => string {
  const { user } = useAuth();
  const canRead = useCan("core.identity.membership.read");
  const directory = useAsync(async (): Promise<ReadonlyMap<string, string>> => {
    if (!canRead) return new Map();
    try {
      const page = await apiRequest<MemberPage>(`/members?size=${DIRECTORY_PAGE_SIZE}`);
      return new Map(page.items.map((m) => [m.user_id, m.display_name || m.full_name]));
    } catch {
      return new Map();
    }
  }, [canRead]);

  const names = useMemo(() => {
    const map = new Map<string, string>(directory.status === "success" ? directory.data : []);
    if (user) map.set(user.user_id, user.full_name);
    return map;
  }, [directory, user]);

  return useCallback(
    (userId: string | null | undefined) =>
      userId ? (names.get(userId) ?? UNKNOWN_MEMBER_NAME) : UNKNOWN_MEMBER_NAME,
    [names],
  );
}
