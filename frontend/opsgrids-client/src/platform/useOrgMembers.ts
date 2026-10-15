/**
 * The organization's members as picker options, for an owner/assignee roster that starts empty.
 * `GET /members` needs `core.identity.membership.read`; without it the list is empty and the
 * picker offers the roster only. A member picked here has no roster row yet, so submit goes
 * through `resolveRosterRef`, which creates it first. Authorization stays on the server.
 */

import { apiRequest } from "../http";
import { useCan } from "../permissions/context";
import { useAsync } from "../useAsync";

export interface OrgMember {
  user_id: string;
  name: string;
}

interface MemberPage {
  items: { user_id: string; full_name: string; display_name: string | null }[];
}

const PREFIX = "m:";

export function useOrgMembers(): OrgMember[] {
  const canRead = useCan("core.identity.membership.read");
  const state = useAsync(async (): Promise<OrgMember[]> => {
    if (!canRead) return [];
    try {
      const page = await apiRequest<MemberPage>("/members?size=200");
      return page.items.map((m) => ({ user_id: m.user_id, name: m.display_name || m.full_name }));
    } catch {
      return [];
    }
  }, [canRead]);
  return state.status === "success" ? state.data : [];
}

/** The roster options, then every org member whose name is not already on the roster. */
export function withOrgMembers<T extends { value: string; label: string }>(
  options: T[],
  members: OrgMember[],
): (T | { value: string; label: string })[] {
  const known = new Set(options.map((o) => o.label.trim().toLowerCase()));
  return [
    ...options,
    ...members
      .filter((m) => !known.has(m.name.trim().toLowerCase()))
      .map((m) => ({ value: `${PREFIX}${m.user_id}`, label: m.name })),
  ];
}

/** A picked value as a roster ref: an org member is added to the roster first. */
export async function resolveRosterRef(
  value: string,
  members: OrgMember[],
  create: (ref: string, name: string) => Promise<unknown>,
): Promise<string> {
  if (!value.startsWith(PREFIX)) return value;
  const id = value.slice(PREFIX.length);
  const member = members.find((m) => m.user_id === id);
  const ref = `u-${id.slice(0, 8)}`;
  if (member) await create(ref, member.name);
  return ref;
}
