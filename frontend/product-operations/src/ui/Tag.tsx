import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A quiet neutral tag for metadata (warehouse, category, unit of measure). Not a status. */
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
