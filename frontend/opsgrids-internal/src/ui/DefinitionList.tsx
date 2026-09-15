import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

export interface Definition {
  term: string;
  value: ReactNode;
}

/** A compact key/value grid built from a real <dl>. Used for account and version summaries. */
export function DefinitionList({
  items,
  className,
}: {
  items: Definition[];
  className?: string;
}) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-4 sm:grid-cols-2", className)}>
      {items.map((item) => (
        <div key={item.term} className="flex flex-col gap-1">
          <dt className="text-xs font-semibold uppercase tracking-wide text-faint">{item.term}</dt>
          <dd className="text-sm font-semibold text-ink">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}
