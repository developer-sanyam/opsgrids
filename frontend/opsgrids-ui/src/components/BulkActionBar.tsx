import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { Button } from "./Button";

interface BulkActionBarProps {
  /** How many rows are ticked. */
  count: number;
  /** Untick everything. */
  onClear: () => void;
  /**
   * The actions, each acting on the selection: "Suspend", "Change plan", "Delete". Outline
   * buttons, and `danger` for the destructive one — which still goes through a `ConfirmDialog`
   * that names how many rows it takes with it.
   */
  children: ReactNode;
  className?: string | undefined;
}

/**
 * What a table's selection can do, shown once there is a selection (brief §7: bulk selection +
 * bulk actions).
 *
 * `DataTable` renders it for you when given `bulkActions` alongside `selection`; it is exported
 * for a list that is not a `DataTable`. It sticks to the top of the scrolling page while rows are
 * being ticked further down, and is a named region, so it is one landmark jump away.
 *
 * The count is not announced from here: a region that mounts carrying its text is not read
 * reliably. `DataTable` keeps a permanent status line for that.
 */
export function BulkActionBar({ count, onClear, children, className }: BulkActionBarProps) {
  return (
    <div
      role="region"
      aria-label="Bulk actions"
      className={cn(
        "sticky top-0 z-20 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-tile bg-accent-soft px-4 py-2",
        className,
      )}
    >
      <p className="tnum text-sm font-semibold text-accent-ink">{count} selected</p>
      <Button variant="ghost" size="sm" onClick={onClear}>
        Clear selection
      </Button>
      <div className="ml-auto flex flex-wrap items-center gap-2">{children}</div>
    </div>
  );
}
