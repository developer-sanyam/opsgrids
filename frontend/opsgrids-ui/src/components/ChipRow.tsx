import { useRef } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { useScrollFade } from "../lib/useScrollFade";

interface ChipRowProps {
  /** The chips, usually `Pill`s. */
  children: ReactNode;
  /** Names the group for assistive tech: "Filter leads by status". */
  label: string;
  className?: string | undefined;
}

/**
 * A row of filter chips that scrolls sideways on a phone and says so: the edge with more chips
 * fades out, so the last visible chip is not mistaken for the end of the list. A row that fits has
 * no fade.
 */
export function ChipRow({ children, label, className }: ChipRowProps) {
  const ref = useRef<HTMLDivElement>(null);
  const fade = useScrollFade(ref);
  return (
    <div
      ref={ref}
      data-fade={fade}
      role="group"
      aria-label={label}
      className={cn("scroll-fade -mx-1 flex items-center gap-2 overflow-x-auto px-1", className)}
    >
      {children}
    </div>
  );
}
