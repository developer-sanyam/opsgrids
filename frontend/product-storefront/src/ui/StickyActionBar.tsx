import type { ReactNode } from "react";

/**
 * The action a long phone screen is for, kept in reach: stuck to the foot of the screen above the
 * bottom navigation, with the page's own content scrolling behind it. Render it only below `md`
 * (`useMediaQuery(TABLET_QUERY, true)`) and keep the desktop control where it was — rendering both
 * and hiding one with CSS would put the control in the document twice.
 *
 * `sticky`, not `fixed`: it stays in the page's flow and takes its own height, so nothing the page
 * ends with slides underneath it. `--shell-bottom-inset` is the bottom nav plus the safe-area
 * inset, which is why the bar is never behind either. The same bar lives in product-operations;
 * it belongs in `@opsgrids/ui` once the workshop has a story and a baseline for it.
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
