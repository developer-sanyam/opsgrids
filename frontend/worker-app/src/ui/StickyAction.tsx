import type { ReactNode } from "react";

/**
 * A screen's one primary action, pinned to the foot of the viewport above the phone's bottom
 * navigation (`.shell-sticky-action`, `@opsgrids/ui`), so a long form never scrolls it out of
 * reach of a thumb. It stays in the document flow, so it never covers the field above it, and on a
 * wide screen — where there is no bottom nav to clear — it simply sits at the foot of the page.
 */
export function StickyAction({ children }: { children: ReactNode }) {
  return <div className="shell-sticky-action border-t border-line bg-ground py-3">{children}</div>;
}
