import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "ghost" | "dark" | "outline" | "danger";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
}

const VARIANTS: Record<Variant, string> = {
  // The single accent, spent on the primary action: violet pill, white text.
  primary:
    "bg-accent text-white hover:bg-accent-strong active:bg-accent-strong disabled:bg-accent/50",
  dark: "bg-ink text-surface hover:bg-ink-soft active:bg-ink-soft disabled:bg-ink/50",
  outline:
    "bg-surface text-ink border border-line hover:bg-ground active:bg-ground disabled:text-faint",
  ghost: "bg-transparent text-ink-soft hover:bg-ground active:bg-ground disabled:text-faint",
  // Destructive operator actions (suspend). Semantic red, never the accent.
  danger:
    "bg-danger text-white hover:opacity-90 active:opacity-90 disabled:opacity-50",
};

const SIZES: Record<Size, string> = {
  sm: "h-9 px-4 text-sm gap-1.5",
  md: "h-11 px-5 text-[0.95rem] gap-2",
};

/** A fully-rounded pill button with all five interactive states. */
export function Button({
  variant = "primary",
  size = "md",
  leadingIcon,
  trailingIcon,
  className,
  children,
  type,
  ...props
}: ButtonProps) {
  return (
    <button
      type={type ?? "button"}
      className={cn(
        "inline-flex items-center justify-center rounded-full font-semibold",
        "transition-colors duration-150 disabled:cursor-not-allowed",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {leadingIcon}
      {children}
      {trailingIcon}
    </button>
  );
}
