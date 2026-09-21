import { useId } from "react";

import { cn } from "../lib/cn";

export interface CheckboxOption {
  value: string;
  label: string;
  /** What choosing this one means, in the user's words — a role's reach, a location's name. */
  description?: string | undefined;
  disabled?: boolean | undefined;
}

interface CheckboxGroupProps {
  /** The `<legend>`. The question the group answers: "Roles", "Locations this member covers". */
  label: string;
  options: CheckboxOption[];
  /** The chosen values. Order does not matter coming in; it is normalised going out. */
  value: string[];
  /** The whole next selection, never a single toggle — the caller stores exactly what it gets. */
  onChange: (next: string[]) => void;
  helper?: string | undefined;
  error?: string | undefined;
  /**
   * `vertical` is the default and is right for anything with descriptions. `horizontal` wraps a
   * row of short labels — days of the week, a handful of statuses.
   */
  orientation?: "vertical" | "horizontal" | undefined;
  className?: string | undefined;
}

/**
 * `RadioGroup`'s many-of counterpart: a real `<fieldset>` of real checkboxes.
 *
 * Reach for it wherever a set is being assembled rather than a single answer chosen — the roles on
 * a membership, the locations a member covers, the permissions an invite carries. Three screens
 * hand-rolled this before it existed, and each one wired `aria-describedby` slightly differently.
 *
 * **It reports the whole next array**, in the order the options are declared, so a caller stores
 * what it is handed instead of merging a toggle into its own state. Values that are not in
 * `options` — a selection made on another page of a filtered list — are kept and appended rather
 * than silently dropped: this component does not get to decide that the caller's data is stale.
 *
 * Keyboard behaviour is the browser's: Tab reaches each box, Space toggles it. Unlike a radio
 * group, arrow keys do not move between checkboxes, and nothing here pretends they do.
 */
export function CheckboxGroup({
  label,
  options,
  value,
  onChange,
  helper,
  error,
  orientation = "vertical",
  className,
}: CheckboxGroupProps) {
  const base = useId();
  const helperId = helper ? `${base}-helper` : undefined;
  const errorId = error ? `${base}-error` : undefined;
  const describedBy = [helperId, errorId].filter(Boolean).join(" ") || undefined;
  const chosen = new Set(value);

  function toggle(optionValue: string, checked: boolean) {
    const next = new Set(chosen);
    if (checked) next.add(optionValue);
    else next.delete(optionValue);
    const known = options.filter((option) => next.has(option.value)).map((option) => option.value);
    const unknown = value.filter(
      (existing) => next.has(existing) && !options.some((option) => option.value === existing),
    );
    onChange([...known, ...unknown]);
  }

  return (
    <fieldset
      className={cn("flex flex-col gap-1.5 border-0 p-0 m-0", className)}
      aria-describedby={describedBy}
      aria-invalid={error !== undefined || undefined}
    >
      <legend className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-faint">
        {label}
      </legend>
      <div
        className={cn(
          "flex gap-2",
          orientation === "horizontal" ? "flex-row flex-wrap" : "flex-col",
        )}
      >
        {options.map((option, index) => {
          const selected = chosen.has(option.value);
          const disabled = option.disabled ?? false;
          // Indexed, not keyed on the value: a value may hold a space ("New York"), and
          // `aria-describedby` is a space-separated token list.
          const descriptionId = option.description ? `${base}-option-${index}-description` : undefined;
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
                type="checkbox"
                value={option.value}
                checked={selected}
                disabled={disabled}
                // The option's own description belongs to the option's box; the group's helper
                // and error belong to the fieldset, where they are read once rather than once
                // per checkbox.
                aria-describedby={descriptionId}
                onChange={(event) => toggle(option.value, event.target.checked)}
                className="mt-0.5 size-4 accent-accent"
              />
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="text-sm font-bold text-ink">{option.label}</span>
                {option.description ? (
                  <span id={descriptionId} className="text-xs text-muted">
                    {option.description}
                  </span>
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
