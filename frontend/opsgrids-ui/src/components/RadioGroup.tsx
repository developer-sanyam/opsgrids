import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

export interface RadioOption {
  value: string;
  label: string;
  /** The consequence of choosing this one, in the user's words. */
  description?: string | undefined;
  disabled?: boolean | undefined;
  /** A `Tag` or similar, shown beside the label. */
  aside?: ReactNode | undefined;
}

interface RadioGroupProps {
  /** The `<legend>`. A radio group is a question, so this is the question. */
  legend: string;
  /** Groups the inputs. Unique per form. */
  name: string;
  value: string | null;
  onChange: (value: string) => void;
  options: RadioOption[];
  helper?: string | undefined;
  error?: string | undefined;
  className?: string | undefined;
}

/**
 * A real `<fieldset>` of real radios.
 *
 * Used where every option must be visible at once and there are few of them — "Change to" on an
 * attendance correction has at most three, and a select would hide the very thing the user is
 * choosing between. Arrow keys move and select within the group; that is the native behaviour and
 * nothing here overrides it.
 */
export function RadioGroup({
  legend,
  name,
  value,
  onChange,
  options,
  helper,
  error,
  className,
}: RadioGroupProps) {
  const base = useId();
  const helperId = helper ? `${base}-helper` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const describedBy = [helperId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <fieldset
      className={cn("flex flex-col gap-1.5 border-0 p-0 m-0", className)}
      aria-describedby={describedBy}
      aria-invalid={error !== undefined || undefined}
    >
      <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-faint">
        {legend}
      </legend>
      <div className="flex flex-col gap-2">
        {options.map((option) => {
          const selected = option.value === value;
          const disabled = option.disabled ?? false;
          return (
            <label
              key={option.value}
              className={cn(
                "flex items-start gap-3 rounded-tile border p-3 transition-colors",
                disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
                selected ? "border-accent bg-accent-soft/50" : "border-line hover:bg-ground",
              )}
            >
              <input
                type="radio"
                name={name}
                value={option.value}
                checked={selected}
                disabled={disabled}
                onChange={() => onChange(option.value)}
                className="mt-0.5 size-4 accent-accent"
              />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex items-center gap-2 text-sm font-bold text-ink">
                  {option.label}
                  {option.aside}
                </span>
                {option.description ? (
                  <span className="text-xs text-muted">{option.description}</span>
                ) : null}
              </span>
            </label>
          );
        })}
      </div>
      {helper ? (
        <p id={helperId} className="text-xs text-muted">
          {helper}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs font-medium text-negative">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
