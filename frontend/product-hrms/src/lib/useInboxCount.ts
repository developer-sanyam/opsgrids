import { useEffect, useState } from "react";

import { listInbox } from "@/api/approvals";

/**
 * The sidebar's "Waiting on me" count (screens spec §1.2) — the quiet grey `NavEntry.count`,
 * never the attention `badge` (a red badge here would recolour Notifications too, since
 * `badgeTone` is sidebar-wide — see gap **G12**). A read failure hides the count rather than
 * showing a stale or wrong number; the nav entry itself never disappears (§2.8).
 */
export function useInboxCount(): string | undefined {
  const [total, setTotal] = useState<number | null>(null);

  useEffect(() => {
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
  }, []);

  return total === null ? undefined : String(total);
}
