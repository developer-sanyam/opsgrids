import type { ReactNode } from "react";

import { usePermission } from "./context";
import type { Scope } from "./scope";

interface RequirePermissionProps {
  /** The full code, `core.approval.policy.write`. */
  perm: string;
  /** The scope the endpoint's guard floors at. Default `OWN`. */
  scope?: Scope;
  /**
   * What to render instead. Usually nothing — but a *screen* that hides its whole reason for
   * existing should say what is possible and who to ask, rather than render an empty page.
   */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Renders its children only when the caller holds `perm` at `scope`.
 *
 * **This hides a control; it does not protect anything.** The route behind it stays reachable by
 * URL and the endpoint behind that refuses the call on its own — which is the intended design, not
 * a gap: a 403 rendered honestly is how an operator finds a missing grant, and hiding the route
 * would make a deploy-order mistake invisible.
 *
 * **Not exported from `src/index.ts`.** `usePermission` reads `grants` from context and returns
 * `false` when the provider has none to give — the ordinary case today, since nothing in the repo
 * produces a caller's grants yet. That makes this component deny-all by default, which is worse
 * than not shipping it: the first screen to mount it would silently show its `fallback` for
 * everyone. Import it directly from here once a real grants source lands, or once `grants` is a
 * required prop on `PermissionProvider` so a deny-all mount fails to typecheck instead of failing
 * silently in production.
 */
export function RequirePermission({
  perm,
  scope = "OWN",
  fallback = null,
  children,
}: RequirePermissionProps) {
  const allowed = usePermission(perm, scope);
  return <>{allowed ? children : fallback}</>;
}
