/**
 * The permission and entitlement hooks.
 *
 * **These hide UI. They are not authorization.** ADR-0003 §3 states the rule this file exists
 * under: the browser is the caller's machine, a hidden button is a `curl` away, and the decision
 * is made only where the query runs — every list endpoint injects scope into the query and every
 * endpoint carries a negative-authz test. Nothing here may ever be the only check.
 *
 * What that means in practice, for anyone reaching for these:
 *
 * - Hiding a control is a courtesy, so the screen must still say what is possible and who to ask.
 * - Prefer letting the **server's own filtering** be the gate. An inbox that returns only what the
 *   caller may decide needs no client-side check at all, and cannot drift from the server's rule.
 * - Never disable a control on a permission. An absent control is honest; a disabled one with no
 *   explanation is a puzzle.
 *
 * Kept in a `.ts` file with no component export so React Fast Refresh stays happy — the provider
 * lives next door in `PermissionProvider.tsx`.
 */

import { createContext, useContext } from "react";

import { hasPermission } from "./scope";
import type { Scope } from "./scope";

/** How the grants fetch itself is doing — distinct from what any one permission answers. */
export type GrantsStatus = "loading" | "ready" | "error";

export interface PermissionState {
  /**
   * The caller's resolved grants as `name:SCOPE`, from `GET /members/me/grants`, with the widest
   * scope already won per code. `null` until they arrive, and `null` again if they could not be
   * read — `status` says which.
   */
  grants: readonly string[] | null;
  /** The `ent` claim: the products this organization has activated. */
  entitlements: readonly string[] | null;
  /**
   * `loading` before the answer arrives, `ready` when it is exact, `error` when the fetch failed.
   *
   * A screen that renders *nothing* where a button should be owes the user a reason. An app shell
   * that reads `error` here and says so turns a screen with no controls into a screen with an
   * explanation — otherwise a transient failure is indistinguishable from a narrow role.
   */
  status: GrantsStatus;
  /** The membership the grants belong to, as the service reported it. */
  membershipId: string | null;
  /** The organization's permission version at the time they were read. */
  pv: number | null;
  /** Why the fetch failed, when it did. */
  error: Error | null;
  /** Ask again — after a role change made elsewhere in the same session. */
  reloadGrants: () => void;
}

export type PermissionStatus = "granted" | "denied" | "unknown";

export const PermissionContext = createContext<PermissionState | null>(null);

const UNMOUNTED: PermissionState = {
  grants: null,
  entitlements: null,
  status: "loading",
  membershipId: null,
  pv: null,
  error: null,
  reloadGrants: () => undefined,
};

/**
 * The raw state, for a screen that needs to tell "denied" from "not known yet" — or to explain a
 * `status: "error"` rather than silently render no controls.
 */
export function usePermissions(): PermissionState {
  return useContext(PermissionContext) ?? UNMOUNTED;
}

/**
 * Does the caller hold `code` at `minimum` or above?
 *
 * Pass the same `minimum` the endpoint's guard passes — `require(perm, minimum=Scope.ORG)` on the
 * server means `usePermission(perm, "ORG")` here. A check on the name alone will show an org-wide
 * control to every employee, because almost everyone holds something at `OWN`.
 */
export function usePermission(code: string, minimum: Scope = "OWN"): boolean {
  const { grants } = usePermissions();
  if (grants === null) return false;
  return hasPermission(grants, code, minimum);
}

/**
 * The same question, answered in three values. Use it where "we don't know yet" should render
 * differently from "no" — a screen that would otherwise hide its own navigation entry and make a
 * deploy-order mistake invisible.
 */
export function usePermissionStatus(code: string, minimum: Scope = "OWN"): PermissionStatus {
  const { grants } = usePermissions();
  if (grants === null) return "unknown";
  return hasPermission(grants, code, minimum) ? "granted" : "denied";
}

/**
 * The gate every Create / Edit / Delete control uses.
 *
 * `true` only on a grant read from `GET /members/me/grants`. While they are loading, and if they
 * could not be read at all, this is `false` and the control renders nothing — fail closed, and
 * never a button that appears and then vanishes once the answer lands.
 *
 * Spelled to read as the sentence it is: `useCan("operations.procurement.supplier.write", "ORG")`.
 * It is `usePermission` under a name a screen can be read aloud with; `usePermissionStatus` is
 * the one to reach for when "not known yet" must render differently from "no".
 *
 * Pass the same `minimum` the endpoint's guard passes. Checking the name alone shows an org-wide
 * control to every employee, because almost everyone holds something at `OWN`.
 *
 * It hides UI. It is not authorization (ADR-0003 §3) — the endpoint refuses the call regardless,
 * so the screen must still handle the 403 it may get.
 */
export function useCan(code: string, minimum: Scope = "OWN"): boolean {
  return usePermissionStatus(code, minimum) === "granted";
}

/**
 * Is this product activated for the organization? Reads the token's `ent` claim, the same fact the
 * gateway gates on — which is why a 402 and this hook agree.
 *
 * Entitlement is not permission: a product can be on the plan and the caller still hold nothing in
 * it, and a platform route (`/api/v1/approvals`) has no product key at all and can never return
 * 402. Do not render a plan-gated state on a 403.
 */
export function useEntitlement(feature: string): boolean {
  const { entitlements } = usePermissions();
  if (entitlements === null) return false;
  return entitlements.includes(feature);
}
