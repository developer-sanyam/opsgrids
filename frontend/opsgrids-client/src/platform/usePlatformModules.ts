/**
 * The products the signed-in caller can actually open — AppShell v2's nav model.
 *
 * A product shows only when both are true: the organization has it activated (the token's `ent`
 * claim) **and** the caller holds at least one grant in it (`GET /members/me/grants`, already
 * loaded by `PermissionProvider`). Entitled-but-no-grant and granted-but-not-entitled are both
 * hidden — an activated product with no role assigned is not a destination for this caller, and a
 * grant surviving a plan downgrade is not a live product either.
 */

import { useMemo } from "react";

import { parseGrant } from "../permissions/scope";
import { usePermissions } from "../permissions/context";
import type { PlatformProductKey } from "./catalogue";
import { PLATFORM_PRODUCTS, getPlatformHost } from "./catalogue";

export interface PlatformModule {
  key: PlatformProductKey;
  label: string;
  iconKey: string;
  /** The product's own host — where the nav entry links to. */
  href: string;
}

function entitledFor(entitlements: readonly string[], keys: readonly string[]): boolean {
  return keys.some((key) => entitlements.includes(key));
}

function grantedFor(grants: readonly string[], keys: readonly string[]): boolean {
  for (const grant of grants) {
    const parsed = parseGrant(grant);
    if (!parsed) continue;
    if (keys.some((key) => parsed.code === key || parsed.code.startsWith(`${key}.`))) return true;
  }
  return false;
}

/**
 * `[]` until both entitlements and grants are known — the same fail-closed reading
 * `usePermissions` uses elsewhere: a nav item that appears once the answer lands is fine, one that
 * appears and then disappears is not.
 */
export function usePlatformModules(): PlatformModule[] {
  const { entitlements, grants } = usePermissions();

  return useMemo(() => {
    if (entitlements === null || grants === null) return [];
    return PLATFORM_PRODUCTS.filter(
      (product) =>
        entitledFor(entitlements, product.entitlementKeys) &&
        grantedFor(grants, product.entitlementKeys),
    ).map((product) => ({
      key: product.key,
      label: product.displayName,
      iconKey: product.iconKey,
      href: getPlatformHost(product.key),
    }));
  }, [entitlements, grants]);
}
