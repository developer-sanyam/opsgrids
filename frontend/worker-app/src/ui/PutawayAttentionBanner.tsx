import { Link, useLocation } from "react-router-dom";

import { InlineAlert } from "@opsgrids/ui";

import { usePutawayQueue } from "@/lib/putawayQueueContext";

/**
 * Put-aways the server refused sit on the phone until someone reads and discards them. A worker
 * who has moved on to another screen would never find out, so the shell says so — until they are on
 * the Put away screen itself, where the same entries are already listed in full.
 */
export function PutawayAttentionBanner() {
  const { attention } = usePutawayQueue();
  const { pathname } = useLocation();
  if (attention.length === 0 || pathname === "/putaway") return null;

  return (
    <InlineAlert
      tone="warning"
      title={`${attention.length} put-away${attention.length === 1 ? "" : "s"} couldn't be recorded`}
      action={
        <Link to="/putaway" className="text-sm font-semibold text-accent hover:underline">
          Review
        </Link>
      }
    >
      The server refused {attention.length === 1 ? "it" : "them"}. Open Put away to see why.
    </InlineAlert>
  );
}
