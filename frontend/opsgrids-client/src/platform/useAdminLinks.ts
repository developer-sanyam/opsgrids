/**
 * AppShell v2's admin section: Users, Settings and Audit Logs, each shown only when the caller
 * holds the core permission its screen requires, the same read-gates `opsgrids-dashboard`'s own
 * `Sidebar` already checks (`core.identity.membership.read`, `core.org.organization.read`,
 * `core.identity.audit.read` at ORG). Hiding these is a courtesy: the destination route still
 * enforces its own permission regardless, and every audit source refuses below ORG scope.
 *
 * Audit Logs opens the dashboard's `/admin/audit`, which merges each service's
 * `GET /api/v1/audit/<service>` (`platform_core.audit_read`).
 */

import { useMemo } from "react";

import { useCan } from "../permissions/context";

export type AdminLinkKey = "users" | "settings" | "audit-logs";

export interface AdminLink {
  key: AdminLinkKey;
  label: string;
  href: string;
}

/** Where the admin console lives today — `opsgrids-dashboard`'s own routes. */
const ADMIN_HREFS: Record<AdminLinkKey, string> = {
  users: "/admin/members",
  settings: "/admin/organization",
  "audit-logs": "/admin/audit",
};

export function useAdminLinks(): AdminLink[] {
  const canReadUsers = useCan("core.identity.membership.read");
  const canReadSettings = useCan("core.org.organization.read");
  const canReadAudit = useCan("core.identity.audit.read", "ORG");

  return useMemo(() => {
    const links: AdminLink[] = [];
    if (canReadUsers) links.push({ key: "users", label: "Users", href: ADMIN_HREFS.users });
    if (canReadSettings) links.push({ key: "settings", label: "Settings", href: ADMIN_HREFS.settings });
    if (canReadAudit) links.push({ key: "audit-logs", label: "Audit Logs", href: ADMIN_HREFS["audit-logs"] });
    return links;
  }, [canReadUsers, canReadSettings, canReadAudit]);
}
