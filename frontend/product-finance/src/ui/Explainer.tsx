import { useId, useState } from "react";
import type { ReactNode } from "react";

/**
 * A screen's orientation paragraph. From 640px it is the plain paragraph it always was; on a
 * phone it collapses to one line with a "Learn more" control, so the data starts above the fold
 * instead of four lines of policy text pushing it down.
 */
export function Explainer({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="-mt-2 flex max-w-2xl flex-col items-start gap-1 text-sm text-muted sm:block">
      <p id={id} className={open ? "" : "line-clamp-1 sm:line-clamp-none"}>
        {children}
      </p>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={() => setOpen((previous) => !previous)}
        className="relative hit-44 rounded-tile font-semibold text-ink-soft underline decoration-dotted underline-offset-2 sm:hidden"
      >
        {open ? "Show less" : "Learn more"}
      </button>
    </div>
  );
}
