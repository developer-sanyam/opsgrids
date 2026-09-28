import type { ReactNode } from "react";

import { RequirePermission } from "@opsgrids/client";
import type { Scope } from "@opsgrids/client";

import { PermissionDenied } from "@/ui/PermissionDenied";

/**
 * A screen gated on one Operations permission. Copied from `product-engage`'s
 * `RequireGrant` — same reasoning: this hides the screen, the service refuses its reads
 * regardless, and it renders nothing while the caller's grants load so a worker who holds the
 * permission never sees the denial flash by.
 */
export function RequireGrant({
  permission,
  scope = "OWN",
  what,
  children,
}: {
  permission: string;
  scope?: Scope | undefined;
  /** What the screen is, in the caller's words: "this task", "packing". */
  what: string;
  children: ReactNode;
}) {
  return (
    <RequirePermission perm={permission} scope={scope} fallback={<PermissionDenied what={what} />}>
      {children}
    </RequirePermission>
  );
}
