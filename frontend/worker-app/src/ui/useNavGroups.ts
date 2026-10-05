import { useMemo } from "react";

import { useCan } from "@opsgrids/client";
import { CheckSquareIcon, HomeIcon, UserIcon, WarehouseIcon } from "@opsgrids/ui";
import type { NavEntry, NavGroup } from "@opsgrids/ui";

// Task detail, scan and pick/pack confirmation are real screens now (docs/fulfilment/plan.md F3)
// but stay out of top-level nav on purpose: each is reached by drilling into a specific task or
// order from Home, the same way an order detail screen is never a nav item of its own. Counts
// (ADR-0026, Fulfilment plan F8) is a genuine top-level destination — a session isn't reached by
// drilling into anything else. So is Put away (storage put-away, ADR-0014 §12): a worker arrives
// with a pallet, not from a task. It is listed only for someone who holds the permission the
// screen is gated on — a hidden entry is a courtesy, `RequireGrant` and the server are the gate.
const HOME: NavEntry = { to: "/", label: "Home", icon: HomeIcon, end: true };
const COUNTS: NavEntry = { to: "/counts", label: "Counts", icon: CheckSquareIcon };
const PUTAWAY: NavEntry = { to: "/putaway", label: "Put away", icon: WarehouseIcon };
const PROFILE: NavEntry = { to: "/profile", label: "Profile", icon: UserIcon };

/**
 * This app's navigation: `groups` for `AppShell`'s `nav` prop, and `bottomExtra` for the phone's
 * bottom bar (`bottomNavExtra`), which `App.tsx` ends with Profile — one tap away rather than
 * behind "More". The bottom bar is Home, Put away (when permitted), Profile, More.
 */
export function useNavGroups(): { groups: NavGroup[]; pinned: NavEntry[]; bottomExtra: NavEntry[] } {
  const canPutAway = useCan("operations.inventory.stock.transfer", "ORG");

  return useMemo(() => {
    const items = canPutAway ? [HOME, COUNTS, PUTAWAY, PROFILE] : [HOME, COUNTS, PROFILE];
    return {
      groups: [{ title: "Main Menu", items }],
      pinned: [],
      bottomExtra: canPutAway ? [PUTAWAY] : [],
    };
  }, [canPutAway]);
}

/** Profile — the last tab on the bottom bar. */
export const PROFILE_NAV_ENTRY = PROFILE;
