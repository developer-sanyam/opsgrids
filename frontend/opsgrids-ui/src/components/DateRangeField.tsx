import { useId } from "react";
import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { DateTimeField } from "./DateTimeField";

interface DateRangeFieldProps {
  /** The `<legend>` — the question the pair answers, e.g. "When it runs". */
  legend: string;
  startLabel?: string | undefined;
  endLabel?: string | undefined;
  start: string | null;
  end: string | null;
  onStartChange: (instant: string | null) => void;
  onEndChange: (instant: string | null) => void;
  /** The IANA zone both wall clocks belong to. */
  zone: string;
  /**
   * The sentence naming that zone — "Times are in Bengaluru time (IST)." or, on an organization
   * fallback, "Times are in organization time (Asia/Kolkata)."
   *
   * Required, and read with **both** fields: without it the pair is ambiguous, and a delegation
   * whose start time is ambiguous is one whose handover cannot be audited.
   */
  zoneNote: ReactNode;
  startError?: string | undefined;
  endError?: string | undefined;
  disabled?: boolean | undefined;
  className?: string | undefined;
}

/**
 * Two zone-aware datetime fields that mean one thing: a range. Grouped in a real `<fieldset>`,
 * with the timezone note tied to both by `aria-describedby` so it is heard with each of them
 * rather than once, after both.
 */
export function DateRangeField({
  legend,
  startLabel = "Starts",
  endLabel = "Ends",
  start,
  end,
  onStartChange,
  onEndChange,
  zone,
  zoneNote,
  startError,
  endError,
  disabled = false,
  className,
}: DateRangeFieldProps) {
  const noteId = useId();

  return (
    <fieldset className={cn("m-0 flex flex-col gap-3 border-0 p-0", className)}>
      <legend className="mb-1 text-xs font-semibold uppercase tracking-wide text-faint">
        {legend}
      </legend>
      <div className="grid gap-3 sm:grid-cols-2">
        <DateTimeField
          label={startLabel}
          value={start}
          onChange={onStartChange}
          zone={zone}
          disabled={disabled}
          describedById={noteId}
          {...(startError !== undefined ? { error: startError } : {})}
        />
        <DateTimeField
          label={endLabel}
          value={end}
          onChange={onEndChange}
          zone={zone}
          disabled={disabled}
          describedById={noteId}
          min={start}
          {...(endError !== undefined ? { error: endError } : {})}
        />
      </div>
      <p id={noteId} className="text-xs text-muted">
        {zoneNote}
      </p>
    </fieldset>
  );
}
