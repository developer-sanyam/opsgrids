import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cn } from "@/lib/cn";

type Variant = "primary" | "ghost" | "dark" | "outline";
type Size = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: Size;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
}

const VARIANTS: Record<Variant, string> = {
  // The single accent, spent on the primary action: mint pill, black text.
  primary:
    "bg-mint text-ink hover:bg-mint-strong active:bg-mint-strong disabled:bg-mint/50",
  dark: "bg-ink text-white hover:bg-ink-soft active:bg-ink-soft disabled:bg-ink/50",
  outline:
    "bg-transparent text-ink border border-line hover:bg-ground active:bg-ground disabled:text-faint",
  ghost:
    "bg-transparent text-ink-soft hover:bg-ground active:bg-ground disabled:text-faint",
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
