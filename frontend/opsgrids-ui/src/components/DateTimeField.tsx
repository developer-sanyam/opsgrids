import type { ReactNode } from "react";

import { instantToWallClock, wallClockToInstant } from "../lib/datetime";
import { fieldSkin } from "../lib/field";
import { FormField } from "./FormField";

interface DateTimeFieldProps {
  label: string;
  /** The instant in UTC (`2026-09-20T03:30:00.000Z`), or null when nothing is chosen yet. */
  value: string | null;
  onChange: (instant: string | null) => void;
  /**
   * The IANA zone the wall clock on screen belongs to. Required, with no default: a datetime
   * field that falls back to the browser's zone collects a different instant for every viewer,
   * which is the precise failure ADR-0000 forbids.
   */
  zone: string;
  helper?: ReactNode | undefined;
  error?: string | undefined;
  /** The earliest instant this field accepts, in UTC. */
  min?: string | null | undefined;
  disabled?: boolean | undefined;
  /** An id of shared copy — the timezone note under a range — appended to `aria-describedby`. */
  describedById?: string | undefined;
  className?: string | undefined;
}

/**
 * A zone-aware datetime field.
 *
 * It collects a **wall clock** — "9:00 am on 20 Sep" — and converts it using the zone it was
 * given, so the instant that reaches the server is the one the user meant in the office they were
 * thinking about. The native control is deliberate: it is keyboard-complete segment by segment
 * with no calendar overlay to escape from, and it carries no timezone of its own to disagree with.
 */
export function DateTimeField({
  label,
  value,
  onChange,
  zone,
  helper,
  error,
  min,
  disabled = false,
  describedById,
  className,
}: DateTimeFieldProps) {
  return (
    <FormField
      label={label}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
      {...(className !== undefined ? { className } : {})}
    >
      {({ id, describedBy, invalid }) => (
        <input
          id={id}
          type="datetime-local"
          disabled={disabled}
          value={value ? instantToWallClock(value, zone) : ""}
          min={min ? instantToWallClock(min, zone) : undefined}
          onChange={(event) => {
            const wall = event.target.value;
            onChange(wall === "" ? null : wallClockToInstant(wall, zone));
          }}
          aria-describedby={[describedBy, describedById].filter(Boolean).join(" ") || undefined}
          aria-invalid={invalid || undefined}
          className={fieldSkin(invalid, "h-11")}
        />
      )}
    </FormField>
  );
}
