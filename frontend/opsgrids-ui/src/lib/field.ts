/**
 * The shared input skin. Lives beside the other helpers rather than in `FormField.tsx` so that
 * file exports components only and Fast Refresh keeps working — the same reason `theme.ts` and
 * `ThemeProvider.tsx` are two files.
 */

import { cn } from "./cn";

/**
 * White fill and a `field-edge` border, which clears 3:1 against every surface — an empty field is
 * identified by its edge alone (WCAG 1.4.11), so the edge cannot be a decorative hairline. The
 * edge darkens on hover, turns negative when invalid, and the accent focus ring sits outside it.
 */
export function fieldSkin(invalid: boolean, className?: string): string {
  return cn(
    "w-full rounded-tile bg-surface px-3 text-sm text-ink placeholder:text-faint",
    "border transition-colors duration-(--duration-quick)",
    invalid ? "border-negative" : "border-field-edge hover:border-muted",
    "focus:outline-none focus-visible:outline-2 focus-visible:outline-focus focus-visible:outline-offset-2",
    "disabled:cursor-not-allowed disabled:border-line disabled:bg-sunken disabled:text-disabled",
    className,
  );
}
