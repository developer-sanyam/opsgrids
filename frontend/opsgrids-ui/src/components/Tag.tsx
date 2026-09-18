import type { ReactNode } from "react";

import { cn } from "../lib/cn";

/**
 * A quiet neutral tag for metadata — an employment type, a warehouse, a plan interval.
 * Not a status: anything with a lifecycle meaning belongs in that product's status pill.
 */
export function Tag({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full bg-ground px-2.5 py-1 text-xs font-medium text-muted",
        className,
      )}
    >
      {children}
    </span>
  );
}
