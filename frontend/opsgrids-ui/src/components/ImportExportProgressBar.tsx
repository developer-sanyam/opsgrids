import { cn } from "../lib/cn";
import { percentOf } from "../lib/importExportCopy";

interface ProgressBarProps {
  /** What is progressing: "Import progress". The bar has no visible label of its own. */
  label: string;
  value: number;
  /** Zero or less means the total is not known yet: the bar reports work without a figure. */
  max: number;
  /** The figures in words, for a screen reader: "12 of 120 rows sent". */
  valueText: string;
}

/**
 * A determinate progress bar for the import and the export. The numbers beside it say the same
 * thing in words, so the bar is never the only carrier. The width eases with `--duration-move`,
 * which `prefers-reduced-motion` sets to zero at the token, and the unknown-total shimmer is
 * `motion-safe` only.
 */
export function ImportExportProgressBar({ label, value, max, valueText }: ProgressBarProps) {
  const known = max > 0;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuetext={valueText}
      {...(known ? { "aria-valuemax": max, "aria-valuenow": Math.min(value, max) } : {})}
      className="h-2 overflow-hidden rounded-full bg-sunken"
    >
      <div
        className={cn(
          "h-full rounded-full bg-accent transition-all duration-(--duration-move)",
          !known && "w-1/3 motion-safe:animate-pulse",
        )}
        {...(known ? { style: { width: `${percentOf(value, max)}%` } } : {})}
      />
    </div>
  );
}
