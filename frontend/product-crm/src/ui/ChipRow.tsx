import { useRef } from "react";
import type { ReactNode } from "react";

import { useScrollFade } from "@opsgrids/ui";

/**
 * A row of filter chips that scrolls sideways on a phone and says so: the edge with more chips
 * fades out, so "Disqualified" and "Converted" are not mistaken for the end of the list.
 */
export function ChipRow({ children, label }: { children: ReactNode; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const fade = useScrollFade(ref);
  return (
    <div
      ref={ref}
      data-fade={fade}
      role="group"
      aria-label={label}
      className="scroll-fade -mx-1 flex items-center gap-2 overflow-x-auto px-1"
    >
      {children}
    </div>
  );
}
