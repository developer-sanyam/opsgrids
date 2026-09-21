import type { ReactNode } from "react";

import { usePermissionStatus } from "./context";
import type { Scope } from "./scope";

interface CanProps {
  /** The full code the endpoint's guard names — `operations.procurement.supplier.write`. */
  permission: string;
  /**
   * The scope that guard floors at. Pass the same one: `require(perm, minimum=Scope.ORG)` on the
   * server is `scope="ORG"` here. Defaults to `OWN`.
   */
  scope?: Scope;
  /**
   * What to render in place of a control the caller is **known** not to hold — usually nothing.
   * Not rendered while the grants are still loading, and not rendered if they failed to load:
   * "You don't have permission to do this" is a claim, and it should only be made when it is
   * true rather than when the network was slow.
   */
  fallback?: ReactNode;
  children: ReactNode;
}

/**
 * Wraps a control only some callers may use: Create, Edit, Delete, Approve.
 *
 *     <Can permission="operations.procurement.supplier.write">
 *       <Button onClick={openCreate}>New supplier</Button>
 *     </Can>
 *
 * Three answers, three renderings: held → the control; known not held → `fallback`; not known
 * yet or unreadable → nothing at all. That last one is why this exists rather than a plain
 * `{allowed && …}`: a button that flashes into view and then disappears when the grants land
 * reads as a bug, and one that appears only after a failed fetch is a 403 waiting to happen.
 *
 * `<RequirePermission>` is the same question asked about a whole screen, where an explanatory
 * fallback is worth rendering.
 *
 * It hides a control; it protects nothing. The endpoint refuses the call on its own (ADR-0003
 * §3), so the screen still has to render the 403 it may get.
 */
export function Can({ permission, scope = "OWN", fallback = null, children }: CanProps) {
  const status = usePermissionStatus(permission, scope);
  if (status === "granted") return <>{children}</>;
  if (status === "denied") return <>{fallback}</>;
  return null;
}
