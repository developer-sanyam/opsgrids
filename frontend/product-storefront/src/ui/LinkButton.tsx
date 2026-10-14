import { Link } from "react-router-dom";
import type { ReactNode } from "react";

import { cn } from "@opsgrids/ui";

/**
 * A router link drawn as the design system's `Button`. The system has no link variant yet, and a
 * navigation that is a `<button>` with an `onClick` is not a link (no open-in-new-tab, no URL in
 * the status bar). It carries the same tokens, size and focus ring `Button` does, so a screen's
 * actions read as one family; when the design system grows a link variant this file is deleted.
 */
const BASE =
  "relative inline-flex min-h-11 max-w-full items-center justify-center gap-2 rounded-tile px-4 py-2 text-center text-sm font-semibold transition-colors duration-(--duration-quick)";

const VARIANT = {
  primary: "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong",
  secondary: "border border-field-edge bg-surface text-ink hover:bg-sunken active:bg-sunken",
} as const;

export function LinkButton({
  to,
  variant = "secondary",
  children,
  className,
}: {
  to: string;
  variant?: keyof typeof VARIANT;
  children: ReactNode;
  className?: string;
}) {
  return (
    <Link to={to} className={cn(BASE, VARIANT[variant], className)}>
      {children}
    </Link>
  );
}
