import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { Button } from "./Button";

export interface DetailItem {
  label: string;
  value: ReactNode;
  /** A quieter line under the value: when it was checked, where it came from. */
  hint?: string | undefined;
}

interface DetailListProps {
  items: DetailItem[];
  /** Two columns from the small breakpoint up; one on a phone either way. Default 1. */
  columns?: 1 | 2 | undefined;
  className?: string | undefined;
}

/** A record's facts as a real `<dl>`: each label is a term, each value its description. */
export function DetailList({ items, columns = 1, className }: DetailListProps) {
  return (
    <dl className={cn("grid grid-cols-1 gap-x-6 gap-y-4", columns === 2 && "sm:grid-cols-2", className)}>
      {items.map((item) => (
        <div key={item.label} className="flex min-w-0 flex-col gap-1">
          <dt className="text-xs font-semibold text-muted">{item.label}</dt>
          <dd className="min-w-0 break-words text-sm text-ink">
            {item.value}
            {item.hint !== undefined ? <span className="mt-1 block text-xs text-faint">{item.hint}</span> : null}
          </dd>
        </div>
      ))}
    </dl>
  );
}

interface PropertyRowProps {
  label: string;
  children: ReactNode;
  /** Shows an Edit button; the editor itself is the app's (an inline field, a dialog). */
  onEdit?: (() => void) | undefined;
  /** The Edit button's accessible name. Default "Edit {label}". */
  editLabel?: string | undefined;
}

/**
 * One label and value on a line, inside a `<dl>`, with an optional Edit. The button says which
 * property it edits, because a column of buttons all called "Edit" is no choice at all.
 */
export function PropertyRow({ label, children, onEdit, editLabel }: PropertyRowProps) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <dt className="text-sm text-muted">{label}</dt>
      <dd className="flex min-w-0 items-center justify-end gap-2 text-right text-sm font-semibold text-ink">
        <span className="min-w-0 break-words">{children}</span>
        {onEdit !== undefined ? (
          <Button variant="ghost" size="sm" aria-label={editLabel ?? `Edit ${label}`} onClick={onEdit}>
            Edit
          </Button>
        ) : null}
      </dd>
    </div>
  );
}
