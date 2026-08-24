import type { ReactNode } from "react";

import { cn } from "@/lib/cn";

/** A quiet neutral tag for metadata (a task label, a work mode). Not a status. */
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
