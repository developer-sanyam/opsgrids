import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { FIELD_GRID } from "../lib/field";

interface FieldGridProps {
  /** The column template: `"sm:grid-cols-3"`. One column until a breakpoint says otherwise. */
  className?: string | undefined;
  children: ReactNode;
}

/**
 * A row (or rows) of form fields whose labels, inputs and helper text line up across the row.
 * Use it wherever fields sit side by side outside a `FormSection` — a line-item editor, a dialog
 * row. A label that wraps or a helper under one field never moves its neighbour's input.
 */
export function FieldGrid({ className, children }: FieldGridProps) {
  return <div className={cn(FIELD_GRID, "grid-cols-1", className)}>{children}</div>;
}
