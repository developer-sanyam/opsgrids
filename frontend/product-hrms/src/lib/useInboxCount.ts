import { useEffect, useState } from "react";

import { usePermissionStatus } from "@opsgrids/client";

import { listInbox } from "@/api/approvals";

/**
 * The sidebar's "Waiting on me" count (screens spec §1.2) — the quiet grey `NavEntry.count`,
 * never the attention `badge` (a red badge here would recolour Notifications too, since
 * `badgeTone` is sidebar-wide — see gap **G12**). A read failure hides the count rather than
 * showing a stale or wrong number; the nav entry itself never disappears (§2.8).
 */
export function useInboxCount(): string | undefined {
  const [total, setTotal] = useState<number | null>(null);
  // A role with no approvals gets a 403 on every page load; known-denied, don't ask. (Unknown --
  // grants still loading -- asks, and a failure still just hides the count.)
  const access = usePermissionStatus("core.approval.request.read", "OWN");

  useEffect(() => {
    if (access === "denied") {
      setTotal(null);
      return undefined;
    }
    let active = true;
    listInbox({ page: 1, size: 1 })
      .then((page) => {
        if (active) setTotal(page.total);
      })
      .catch(() => {
        if (active) setTotal(null);
      });
    return () => {
      active = false;
    };
  }, [access]);

  return total === null ? undefined : String(total);
}
