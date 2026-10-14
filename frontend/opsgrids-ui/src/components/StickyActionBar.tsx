import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { TABLET_QUERY, useMediaQuery } from "../lib/useMediaQuery";

interface StickyActionBarProps {
  /** The screen's primary action, usually one `Button`. Give it `className="w-full"` to fill the bar. */
  children: ReactNode;
  /**
   * Something to keep in view beside the action: a running total, a save status. It takes the
   * space the action does not; put an `aria-live` on it if it changes without a click.
   */
  summary?: ReactNode;
  /**
   * Render at every width. By default the bar exists only below `md`, because the desktop page
   * keeps its own button where it was, and rendering both would put the control in the document
   * twice. A phone-first app (the worker app) has no desktop layout and sets this.
   */
  always?: boolean | undefined;
  /**
   * Run edge to edge across the page's side padding (the default), so the bar reads as a strip
   * rather than a floating box. Turn it off where the page has no side padding to cancel.
   */
  bleed?: boolean | undefined;
  className?: string | undefined;
}

/**
 * The one action a long phone screen is for, kept in reach: stuck to the foot of the screen above
 * the bottom navigation, with the page's own content scrolling behind it.
 *
 * `sticky`, not `fixed`: it stays in the page's flow and takes its own height, so nothing the page
 * ends with slides underneath it. `.shell-sticky-action` sets `bottom: var(--shell-bottom-inset)`,
 * which is the bottom nav plus the safe-area inset, so the bar clears both. `data-action-bar` lets
 * `console.css` widen the page's scroll-padding so a focused field is not left under the bar.
 */
export function StickyActionBar({ children, summary, always = false, bleed = true, className }: StickyActionBarProps) {
  const roomy = useMediaQuery(TABLET_QUERY, true);
  if (roomy && !always) return null;
  return (
    <div
      data-action-bar=""
      className={cn(
        "shell-sticky-action flex items-center gap-3 border-t border-line bg-surface py-3",
        bleed && "-mx-3 px-4",
        className,
      )}
    >
      {summary !== undefined ? <div className="min-w-0 flex-1">{summary}</div> : null}
      <div className={cn("flex items-center", summary === undefined ? "min-w-0 flex-1" : "shrink-0")}>{children}</div>
    </div>
  );
}
