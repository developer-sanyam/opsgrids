/**
 * What this module gates on, and — more importantly — what gating means here.
 *
 * **A hidden control is presentation. It is not security** (ADR-0003 §3, ADR-0010 §9). Every route
 * in the delivery module is guarded server-side at `minimum=Scope.ORG` and carries a negative-authz
 * test; assume every action below is also reachable by a direct request, because it is. Nothing on
 * this screen is the only check on anything.
 *
 * The four permissions are ADR-0010 §9's, verbatim. All four are `default ORG, max ORG` — a
 * delivery zone has no owner, no team and no location reading — so every check here passes `"ORG"`
 * as the floor, matching the guard on the server exactly. Checking the name alone would show an
 * org-wide control to anyone holding anything at `OWN`.
 */

import { usePermissions, usePermissionStatus } from "@opsgrids/client";
import type { PermissionStatus } from "@opsgrids/client";

export const ZONE_READ = "storefront.delivery.zone.read";
export const ZONE_WRITE = "storefront.delivery.zone.write";
export const STORE_SETTINGS_READ = "storefront.store.settings.read";

/** The product key on the `ent` claim and on the gateway's route entry (ADR-0010 §1, §4). */
export const STOREFRONT_PRODUCT = "storefront";

export interface DeliveryAccess {
  /** `granted` | `denied` | `unknown` for `storefront.delivery.zone.read` at ORG. */
  read: PermissionStatus;
  /** Same, for `storefront.delivery.zone.write`. The demand report folds into `read` (§9). */
  write: PermissionStatus;
  /**
   * Is the storefront product activated for this organization?
   *
   * Three values, not two. `null` is "the browser has not been told", which is the ordinary state
   * — the access token's `ent` claim is the only source and a fixture session has no token. A
   * screen that collapsed `null` to `false` would show every merchant a plan-gated wall on a
   * product they have paid for, so this stays honest and the screens read it as "don't know".
   */
  entitled: boolean | null;
}

/**
 * Read the caller's standing on the delivery module.
 *
 * On `unknown` the screens render the control and let the server answer. That is the opposite of
 * the usual advice — hide what you are confident the caller cannot use — and it is right here for
 * a specific reason: nothing in this app mounts a `PermissionProvider`, so every permission reads
 * as `unknown` today, and hiding on `unknown` would hide the entire delivery admin from a merchant
 * who holds every grant. A 403 from the server is rendered as its own state, with copy that names
 * the permission to ask for. When a real grants source lands, `denied` starts arriving and the
 * controls disappear before the round trip rather than after it.
 */
export function useDeliveryAccess(): DeliveryAccess {
  const read = usePermissionStatus(ZONE_READ, "ORG");
  const write = usePermissionStatus(ZONE_WRITE, "ORG");
  const { entitlements } = usePermissions();

  return {
    read,
    write,
    entitled: entitlements === null ? null : entitlements.includes(STOREFRONT_PRODUCT),
  };
}

/** May this control be rendered at all? Everything but a confident "no". */
export function allows(status: PermissionStatus): boolean {
  return status !== "denied";
}
