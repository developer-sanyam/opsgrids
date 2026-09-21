import type { ReactNode } from "react";

import { usePermissions, usePermissionStatus } from "./context";
import type { Scope } from "./scope";

interface RequirePermissionProps {
  /** The full code, `core.approval.policy.write`. */
  perm: string;
  /** The scope the endpoint's guard floors at. Default `OWN`. */
  scope?: Scope;
  /**
   * What to render instead. A *screen* that hides its whole reason for existing should say what
   * is possible and who to ask, rather than render an empty page — so unlike `<Can>`, this one
   * renders the fallback on a failed grants fetch too. A blank screen explains nothing; "we
   * couldn't check your access, try reloading" explains something.
   */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Gates a whole screen or route on a permission.
 *
 * Backed by `GET /members/me/grants` through `PermissionProvider`, so this now answers from the
 * caller's real grants rather than denying everyone — which is why it is exported again. Mount
 * `PermissionProvider` inside `AuthProvider` or every mount of this is a fallback.
 *
 * Renders **nothing** while the grants load, so a screen does not flash its "you can't be here"
 * state on the way in. On a known denial, and on a failed fetch, it renders `fallback`.
 *
 * **This hides a screen; it does not protect anything.** The route stays reachable by URL and the
 * endpoints behind it refuse on their own — which is the intended design, not a gap: a 403
 * rendered honestly is how an operator finds a missing grant, and hiding the route entirely would
 * make a deploy-order mistake invisible. For a single control inside a screen, use `<Can>`.
 */
export function RequirePermission({
  perm,
  scope = "OWN",
  fallback = null,
  children,
}: RequirePermissionProps) {
  const status = usePermissionStatus(perm, scope);
  const { status: grantsStatus } = usePermissions();
  if (status === "granted") return <>{children}</>;
  if (status === "denied" || grantsStatus === "error") return <>{fallback}</>;
  return null;
}
