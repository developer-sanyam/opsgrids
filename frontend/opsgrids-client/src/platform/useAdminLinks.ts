/**
 * AppShell v2's admin section: Users and Settings — each shown only when the caller
 * holds the core permission its screen requires, the same read-gates `opsgrids-dashboard`'s own
 * `Sidebar` already checks (`core.identity.membership.read`, `core.org.organization.read`). Hiding these is a courtesy:
 * the destination route still enforces its own permission regardless.
 *
 * There is deliberately no Audit Logs link: no organization-scoped audit read API exists yet
 * (`/admin/audit` on console is the operator-only trail), so the page it pointed at was a 404.
 */

import { useMemo } from "react";

import { useCan } from "../permissions/context";

export type AdminLinkKey = "users" | "settings";

export interface AdminLink {
  key: AdminLinkKey;
  label: string;
  href: string;
}

/** Where the admin console lives today — `opsgrids-dashboard`'s own routes. */
const ADMIN_HREFS: Record<AdminLinkKey, string> = {
  users: "/admin/members",
  settings: "/admin/organization",
};

export function useAdminLinks(): AdminLink[] {
  const canReadUsers = useCan("core.identity.membership.read");
  const canReadSettings = useCan("core.org.organization.read");

  return useMemo(() => {
    const links: AdminLink[] = [];
    if (canReadUsers) links.push({ key: "users", label: "Users", href: ADMIN_HREFS.users });
    if (canReadSettings) links.push({ key: "settings", label: "Settings", href: ADMIN_HREFS.settings });
    return links;
  }, [canReadUsers, canReadSettings]);
}
