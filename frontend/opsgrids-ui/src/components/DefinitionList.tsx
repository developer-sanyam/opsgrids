import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export interface Definition {
  /** The label. Rendered as a `<dt>`, so it is the term a screen reader announces first. */
  term: string;
  value: ReactNode;
}

interface DefinitionListProps {
  items: Definition[];
  /**
   * Layout classes for the grid — `sm:grid-cols-3` for a wider summary. The default is one column
   * on a phone and two from `sm`, which is what a record's summary card wants.
   */
  className?: string | undefined;
}

/**
 * A compact key/value grid built from a real `<dl>`, for a record's summary: an account, a plan
 * version, a sender. Each pair is its own `<div>` so the term and its value stay together when
 * the grid wraps — HTML allows that grouping, and it keeps a two-column layout from separating
 * a label from the value beneath it.
 */
export function DefinitionList({ items, className }: DefinitionListProps) {
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
