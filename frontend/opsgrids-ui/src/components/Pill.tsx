import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "../lib/cn";

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean | undefined;
  leadingIcon?: ReactNode | undefined;
}

/**
 * A pill toggle — the workhorse of this design language, used for filter chips.
 * Active = violet fill with white text; inactive = quiet ghost that fills on hover.
 */
export function Pill({
  active = false,
  leadingIcon,
  className,
  children,
  type,
  ...props
}: PillProps) {
  return (
    <button
      type={type ?? "button"}
      aria-pressed={active}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-4 h-9 text-sm font-semibold whitespace-nowrap",
        "transition-colors duration-150",
        active
          ? "bg-accent text-white"
          : "text-muted hover:bg-ground hover:text-ink",
        className,
      )}
      {...props}
    >
      {leadingIcon}
      {children}
    </button>
  );
}
