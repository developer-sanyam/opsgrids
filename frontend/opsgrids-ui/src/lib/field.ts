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
export const FIELD_LABEL = "text-body-sm font-semibold text-ink-soft";

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

/**
 * The layout a row of fields shares. Every `FormField` is three rows (label, control, messages),
 * and in this grid each one spans three rows of the parent, so the rows line up across the
 * fields: a label that wraps ("Estimated unit price (optional)") makes its neighbours' label rows
 * taller too, instead of pushing its own input down out of line, and helper text under one input
 * does not move the input beside it.
 *
 * Only a `FormField` (it carries `data-field`) takes part; anything else in the grid is laid out
 * as an ordinary cell.
 */
export const FIELD_GRID = [
  // No row gap: the space between fields is each field's own bottom padding, so a field with no
  // helper text (an empty third row) takes no more room than it did before the rows were shared.
  // The grid's own `-mb-4` takes back the last row's padding.
  "grid items-start gap-x-4 gap-y-0 -mb-4",
  "[&>[data-field]]:row-span-3! [&>[data-field]]:grid! [&>[data-field]]:grid-rows-subgrid!",
  "[&>[data-field]]:gap-y-0! [&>[data-field]]:pb-4",
  // The label-to-input and input-to-message spaces, which the flex layout got from `gap-1.5`.
  "[&>[data-field]>:first-child]:mb-1.5 [&>[data-field]>:nth-child(3)]:mt-1.5",
  "[&>:not([data-field])]:mb-4",
].join(" ");
