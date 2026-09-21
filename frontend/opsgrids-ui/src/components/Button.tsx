import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { cn } from "../lib/cn";

export type ButtonVariant = "primary" | "ghost" | "dark" | "outline" | "danger";
export type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  leadingIcon?: ReactNode;
  trailingIcon?: ReactNode;
  /**
   * The sixth state, for anything that hits the network.
   *
   * Disables the button and marks it `aria-busy`, so a second click cannot land and a screen
   * reader is told the control is working rather than broken. The label stays put — the spinner
   * sits over it and the text keeps its space, because a button that changes width mid-submit
   * moves the Cancel button out from under the pointer.
   *
   * Change the label too where the wait is long enough to need a word ("Saving…"): the spinner
   * says *something* is happening, the label says what.
   */
  loading?: boolean;
  /** For a dialog that wants focus on Cancel, and for returning focus after a row disappears. */
  ref?: Ref<HTMLButtonElement>;
}

const VARIANTS: Record<ButtonVariant, string> = {
  // The single accent, spent on the primary action: violet pill, white text.
  primary:
    "bg-accent text-white hover:bg-accent-strong active:bg-accent-strong disabled:bg-accent/50",
  dark: "bg-ink text-surface hover:bg-ink-soft active:bg-ink-soft disabled:bg-ink/50",
  outline:
    "bg-surface text-ink border border-line hover:bg-ground active:bg-ground disabled:text-faint",
  ghost:
    "bg-transparent text-ink-soft hover:bg-ground active:bg-ground disabled:text-faint",
  // Destructive actions. Semantic red, never the accent.
  danger:
    "bg-negative text-white hover:opacity-90 active:opacity-90 disabled:opacity-50",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-9 px-4 text-sm",
  md: "h-11 px-5 text-[0.95rem]",
};

const GAPS: Record<ButtonSize, string> = {
  sm: "gap-1.5",
  md: "gap-2",
};

/**
 * The working indicator. `currentColor` so it reads on every variant, and `spin` rather than
 * Tailwind's `animate-spin` because that class has no reduced-motion answer — `spin` slows to a
 * crawl under `prefers-reduced-motion` instead of strobing (see `console.css`).
 */
function Spinner({ size }: { size: ButtonSize }) {
  return (
    <svg
      className={cn("spin", size === "sm" ? "size-4" : "size-[18px]")}
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="2.5" opacity="0.25" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** A fully-rounded pill button with all five interactive states — and a sixth when it loads. */
export function Button({
  variant = "primary",
  size = "md",
  leadingIcon,
  trailingIcon,
  loading = false,
  className,
  children,
  type,
  disabled,
  ref,
  ...props
}: ButtonProps) {
  return (
    <button
      ref={ref}
      type={type ?? "button"}
      disabled={disabled === true || loading}
      aria-busy={loading || undefined}
      className={cn(
        "relative inline-flex items-center justify-center rounded-full font-semibold",
        "transition-colors duration-150 disabled:cursor-not-allowed",
        SIZES[size],
        VARIANTS[variant],
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="absolute inset-0 grid place-items-center">
          <Spinner size={size} />
        </span>
      ) : null}
      <span className={cn("inline-flex items-center", GAPS[size], loading && "invisible")}>
        {leadingIcon}
        {children}
        {trailingIcon}
      </span>
    </button>
  );
}
