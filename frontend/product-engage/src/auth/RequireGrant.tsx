import type { ReactNode } from "react";

import { RequirePermission } from "@opsgrids/client";

import { PermissionDenied } from "@opsgrids/ui";

/**
 * A screen gated on one Engage permission, floored at the organization like every Engage grant.
 *
 * The gate hides the screen; the service refuses its reads regardless. It renders nothing while
 * the caller's grants load, so a member who holds the permission never sees the denial flash by.
 */
export function RequireGrant({
  permission,
  what,
  children,
}: {
  permission: string;
  /** What the screen is, in the plural, for the denial's sentence: "campaigns". */
  what: string;
  children: ReactNode;
}) {
  return (
    <RequirePermission perm={permission} scope="ORG" fallback={<PermissionDenied what={what} />}>
      {children}
    </RequirePermission>
  );
}
