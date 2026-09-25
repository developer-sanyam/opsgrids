/**
 * The shared input skin. Lives beside the other helpers rather than in `FormField.tsx` so that
 * file exports components only and Fast Refresh keeps working — the same reason `theme.ts` and
 * `ThemeProvider.tsx` are two files.
 */

import { cn } from "./cn";

/**
 * A field's visible label. One string, so a control that cannot use `FormField`'s `<label>` — a
 * drop zone, which is a button named by more than its label — still looks exactly like its
 * neighbours, and a restyle of form labels lands on both at once.
 */
export const FIELD_LABEL = "text-xs font-semibold uppercase tracking-wide text-faint";

/** Quiet ground fill, hairline on hover, the global violet focus ring, a negative edge when invalid. */
export function fieldSkin(invalid: boolean, className?: string): string {
  return cn(
    "w-full rounded-tile bg-ground px-3 text-sm text-ink placeholder:text-faint",
    "border transition-colors",
    invalid ? "border-negative" : "border-transparent hover:border-line",
    "focus:outline-none focus-visible:outline-2 focus-visible:outline-accent focus-visible:outline-offset-2",
    "disabled:cursor-not-allowed disabled:text-faint",
    className,
  );
}
