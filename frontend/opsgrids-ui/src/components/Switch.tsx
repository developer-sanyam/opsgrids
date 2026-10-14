import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";

interface SwitchProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  /** Visible, always. A switch with only an icon is a guess. */
  label: string;
  /**
   * The consequence of the current position, tied by `aria-describedby` so it is read *with* the
   * control rather than after it. Swap the text on toggle and a screen reader hears the new
   * meaning, which is the entire reason this is a prop and not a paragraph underneath.
   */
  description?: ReactNode | undefined;
  disabled?: boolean | undefined;
  className?: string | undefined;
}

/**
 * A real `role="switch"` with `aria-checked` — an on/off setting that takes effect where it is
 * described, not a checkbox that waits for a Save. (The screens that use it still have a Save;
 * what the switch changes immediately is the explanation beneath it.)
 */
export function Switch({
  checked,
  onChange,
  label,
  description,
  disabled = false,
  className,
}: SwitchProps) {
  const base = useId();
  const labelId = `${base}-label`;
  const descriptionId = description ? `${base}-description` : undefined;

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-center justify-between gap-4">
        <span id={labelId} className="text-sm font-semibold text-ink">
          {label}
        </span>
        <button
          type="button"
          role="switch"
          aria-checked={checked}
          aria-labelledby={labelId}
          aria-describedby={descriptionId}
          disabled={disabled}
          onClick={() => onChange(!checked)}
          className={cn(
            "hit-44 relative h-6 w-11 shrink-0 rounded-full transition-colors duration-(--duration-quick)",
            // The off track is a control edge, not a divider: `field-edge` clears 3:1, `line` did not.
            checked ? "bg-accent" : "bg-field-edge",
            disabled && "cursor-not-allowed opacity-50",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "absolute top-0.5 left-0.5 size-5 rounded-full bg-surface shadow-card transition-transform duration-(--duration-move)",
              checked ? "translate-x-5" : "translate-x-0",
            )}
          />
        </button>
      </div>
      {description ? (
        // No `aria-live` here: the description is already tied to the switch by
        // `aria-describedby`, so a screen reader announces its current text the moment the
        // switch itself receives focus or its state changes — the switch's own name/state
        // announcement already covers that. A live region on top would announce it a second
        // time on every toggle, racing the screen's own `Announcer` for the same change.
        <p id={descriptionId} className="text-xs text-muted">
          {description}
        </p>
      ) : null}
    </div>
  );
}
