import { useEffect, useRef } from "react";

import { cn } from "../lib/cn";
import { useAnnounce } from "../lib/announcer";
import { Button } from "./Button";

interface LoadMoreProps {
  /** Rows on screen now. */
  loaded: number;
  /** The server's `total` — never the loaded length, which is a different number. */
  total: number;
  onLoadMore: () => void;
  loading?: boolean | undefined;
  /**
   * What is being counted, plural: "requests", "employees". Used in the closing line and the
   * announcement, so a user hears what they reached the end of.
   */
  noun: string;
  className?: string | undefined;
}

/**
 * Server paging, appended.
 *
 * Deliberately not infinite scroll: an approver needs to reach the end of a queue and *know* they
 * did, which is what the closing line is for. The button keeps focus while more rows arrive and
 * announces what it added; when the last page lands the button is gone, so focus moves to the
 * line that replaced it rather than to `<body>`.
 */
export function LoadMore({
  loaded,
  total,
  onLoadMore,
  loading = false,
  noun,
  className,
}: LoadMoreProps) {
  const announce = useAnnounce();
  const closingRef = useRef<HTMLParagraphElement>(null);
  const previousLoaded = useRef(loaded);
  const requested = useRef(false);

  const remaining = Math.max(total - loaded, 0);
  const exhausted = remaining === 0;

  useEffect(() => {
    if (loaded === previousLoaded.current) return;
    const added = loaded - previousLoaded.current;
    previousLoaded.current = loaded;
    // Only speak for a page this control asked for — not for a filter change that reset the list.
    if (!requested.current || added <= 0) {
      requested.current = false;
      return;
    }
    requested.current = false;
    announce(`Loaded ${added} more. Showing ${loaded} of ${total} ${noun}.`);
    if (loaded >= total) closingRef.current?.focus();
  }, [loaded, total, noun, announce]);

  if (exhausted) {
    return (
      <p
        ref={closingRef}
        tabIndex={-1}
        className={cn("text-center text-sm text-faint focus:outline-none", className)}
      >
        Showing all {total} {noun}
      </p>
    );
  }

  return (
    <div className={cn("flex justify-center pt-2", className)}>
      <Button
        variant="dark"
        disabled={loading}
        onClick={() => {
          requested.current = true;
          onLoadMore();
        }}
      >
        {loading ? "Loading…" : `Load more · ${remaining} left`}
      </Button>
    </div>
  );
}
