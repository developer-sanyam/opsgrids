import { useMemo } from "react";
import type { ReactNode } from "react";

import { useAuth } from "../auth/context";
import { getAuthOrigin } from "../auth/origin";
import { NotificationsBell } from "../notifications/NotificationsBell";
import { usePermissions } from "../permissions/context";
import { useAdminLinks } from "./useAdminLinks";
import { useCurrentUser } from "./useCurrentUser";
import { usePlatformModules } from "./usePlatformModules";

/** `app.opsgrids.com`'s own routes — see `getAuthOrigin`: `null` (dev, same-origin) falls back to
 *  the production dashboard, exactly the literal every admin link used before. */
const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

export interface ProductShellData {
  /** Who is signed in, for the account menu; `null` between sign-in and `/auth/me` answering. */
  user: { name: string; email: string; roleLabel: string | null } | null;
  onSignOut: () => void;
  /** Other products this caller can open, with the icon *key* the design system resolves. */
  modules: { key: string; label: string; href: string; iconKey: string }[];
  /** Users / Settings / Audit Logs, as absolute links on the dashboard's origin. */
  adminLinks: { key: string; label: string; href: string }[];
  grants: { status: string; reload: () => void };
  /** The header bell, or `undefined` when the app asked not to have one. */
  notifications: ReactNode;
}

/**
 * Everything a product app's shell reads from the platform, in one hook: the signed-in user, sign
 * out, the cross-product modules, the admin links and the grants' health. The app hands it to
 * `ProductShell` (`@opsgrids/ui`), which owns the arrangement.
 *
 * `notifications: false` leaves the bell out — for sample-data mode, where the feed is a gateway
 * read with no sample answer.
 */
export function useProductShell({ notifications = true }: { notifications?: boolean } = {}): ProductShellData {
  const { logout } = useAuth();
  const currentUser = useCurrentUser();
  const modules = usePlatformModules();
  const adminLinks = useAdminLinks();
  const { status, reloadGrants } = usePermissions();
  const adminOrigin = getAuthOrigin() ?? DASHBOARD_ORIGIN_FALLBACK;

  return useMemo(
    () => ({
      user: currentUser
        ? { name: currentUser.name, email: currentUser.email, roleLabel: currentUser.roleDisplay }
        : null,
      onSignOut: () => void logout(),
      modules: modules.map((m) => ({ key: m.key, label: m.label, href: m.href, iconKey: m.iconKey })),
      adminLinks: adminLinks.map((l) => ({ key: l.key, label: l.label, href: `${adminOrigin}${l.href}` })),
      grants: { status, reload: reloadGrants },
      notifications: notifications ? <NotificationsBell /> : undefined,
    }),
    [currentUser, logout, modules, adminLinks, adminOrigin, status, reloadGrants, notifications],
  );
}
