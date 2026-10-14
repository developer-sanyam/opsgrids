import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "../lib/cn";

interface PillProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  active?: boolean | undefined;
  leadingIcon?: ReactNode | undefined;
}

/**
 * A filter toggle chip. Active = accent fill; inactive = a quiet ghost that fills on hover.
 * `aria-pressed` carries the state, so it never rests on the fill alone.
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
        "hit-44 relative inline-flex h-9 shrink-0 items-center gap-1.5 rounded-tile px-3 text-sm font-semibold whitespace-nowrap",
        "transition-colors duration-(--duration-quick)",
        active ? "bg-accent text-on-accent" : "text-muted hover:bg-sunken hover:text-ink",
        className,
      )}
      {...props}
    >
      {leadingIcon}
      {children}
    </button>
  );
}
