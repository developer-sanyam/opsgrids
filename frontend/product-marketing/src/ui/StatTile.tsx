import type { ReactNode } from "react";

import { cn } from "@atmora/ui";

interface StatTileProps {
  label: string;
  value: string;
  sub?: ReactNode;
  className?: string;
}

/** A compact labelled metric: a quiet uppercase label over a big tabular value, plus an optional
 * sub-line. Used in clusters on the campaign detail and channel screens. */
export function StatTile({ label, value, sub, className }: StatTileProps) {
  return (
    <div
      className={cn(
        "flex flex-col gap-1 rounded-tile border border-line bg-surface p-4",
        className,
      )}
    >
      <span className="text-[0.7rem] font-semibold uppercase tracking-wide text-faint">
        {label}
      </span>
      <span className="display text-xl font-extrabold tnum leading-none text-ink">{value}</span>
      {sub ? <span className="text-xs text-muted">{sub}</span> : null}
    </div>
  );
}
