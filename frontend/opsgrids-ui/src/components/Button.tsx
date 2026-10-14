import type { ButtonHTMLAttributes, ReactNode, Ref } from "react";

import { cn } from "../lib/cn";

/**
 * The hierarchy (owner brief §10). Not every action is primary.
 *
 * - `primary`: the one action a screen exists for. Create, save, submit, continue, approve.
 * - `secondary`: everything that sits beside it. Cancel, back, filter, export.
 * - `ghost`: a quiet action inside a card or a row ("View all", "Add row").
 * - `destructive`: delete, suspend, remove, an irreversible cancel. Nothing else.
 * - `dark`: an ink-filled emphasis for a surface where the accent is already spent.
 *
 * `outline` and `danger` are the names these had before the brief; they draw exactly
 * `secondary` and `destructive` and are kept until the call sites are renamed.
 */
export type ButtonVariant =
  | "primary"
  | "secondary"
  | "ghost"
  | "destructive"
  | "dark"
  /** @deprecated Write `secondary`. */
  | "outline"
  /** @deprecated Write `destructive`. */
  | "danger";
export type ButtonSize = "sm" | "md";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant | undefined;
  size?: ButtonSize | undefined;
  leadingIcon?: ReactNode | undefined;
  trailingIcon?: ReactNode | undefined;
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
  loading?: boolean | undefined;
  /** For a dialog that wants focus on Cancel, and for returning focus after a row disappears. */
  ref?: Ref<HTMLButtonElement> | undefined;
}

const SECONDARY =
  "bg-surface text-ink border border-field-edge hover:bg-sunken active:bg-sunken " +
  "disabled:border-line disabled:bg-surface disabled:text-disabled";
const DESTRUCTIVE =
  "bg-negative text-on-negative hover:opacity-90 active:opacity-90 disabled:opacity-50";

const VARIANTS: Record<ButtonVariant, string> = {
  // The single accent, spent on the primary action.
  primary:
    "bg-accent text-on-accent hover:bg-accent-strong active:bg-accent-strong disabled:bg-accent/50",
  // A control edge, not a divider: the same `field-edge` an input carries.
  secondary: SECONDARY,
  outline: SECONDARY,
  ghost:
    "bg-transparent text-ink-soft hover:bg-sunken hover:text-ink active:bg-sunken disabled:text-disabled disabled:hover:bg-transparent",
  // Destructive actions. Semantic red, never the accent.
  destructive: DESTRUCTIVE,
  danger: DESTRUCTIVE,
  dark: "bg-ink text-surface hover:bg-ink-soft active:bg-ink-soft disabled:bg-ink/50",
};

const SIZES: Record<ButtonSize, string> = {
  // 36px: dense toolbars and table rows.
  sm: "h-9 px-3 text-sm",
  // 44px: the default, and the touch target the brief asks for on a phone.
  md: "h-11 px-4 text-sm",
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
      className={cn("spin", size === "sm" ? "size-4" : "size-4.5")}
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

/** A rounded-rectangle button with all five interactive states — and a sixth when it loads. */
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
        // `shrink-0`: a flex row squeezes a button below its own label, the label then wraps inside the
        // pill and grows past its fixed height and out of its card ("Import / Export" on a phone). A label
        // too long for any row still wraps, in a button that is not in a flex row.
        "relative inline-flex items-center justify-center shrink-0 rounded-tile font-semibold",
        "transition-colors duration-(--duration-quick) disabled:cursor-not-allowed",
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
      {/*
        `opacity-0`, not `invisible`: `visibility: hidden` takes the text out of the accessibility
        tree, and the label is the button's accessible name — a loading button with no name is
        what axe flagged on every pending dialog.
      */}
      <span className={cn("inline-flex items-center", GAPS[size], loading && "opacity-0")}>
        {leadingIcon}
        {children}
        {trailingIcon}
      </span>
    </button>
  );
}
