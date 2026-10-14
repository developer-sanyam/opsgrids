import type { ReactNode } from "react";

/**
 * The one action a long phone screen is for, kept in reach: stuck to the foot of the screen,
 * above the bottom navigation, with the page's own content scrolling behind it. Render it only
 * below `md` (`useMediaQuery(TABLET_QUERY, true)`) and keep the desktop button where it was —
 * rendering both and hiding one with CSS would put the control in the document twice.
 *
 * `sticky`, not `fixed`: it stays inside the page's flow, so it takes its own height and nothing
 * the page ends with slides underneath it. `--shell-bottom-inset` is the bottom nav plus the
 * safe-area inset, which is why the bar is never behind either.
 */
export function StickyActionBar({ children }: { children: ReactNode }) {
  return (
    <div
      className="sticky z-10 -mx-3 flex items-center gap-3 border-t border-line bg-surface px-4 py-3"
      style={{ bottom: "var(--shell-bottom-inset)" }}
    >
      {children}
    </div>
  );
}
