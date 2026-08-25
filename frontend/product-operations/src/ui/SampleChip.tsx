import { cn } from "@/lib/cn";

/**
 * A small amber chip that marks a surface as representative rather than live — used on the
 * trend history (KPI deltas / sparklines and the spend-over-time series) while there is no
 * reporting service to serve it. Amber (not the accent, not an error red) reads as
 * "informational, provisional".
 */
interface SampleChipProps {
  className?: string;
  /**
   * What is representative. Defaults to "Sample data" — the whole widget. Pass "Sample trend"
   * where the headline number is live and only its delta and sparkline are stand-ins.
   */
  label?: string;
}

export function SampleChip({ className, label = "Sample data" }: SampleChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late",
        className,
      )}
      title="Representative data — there is no reporting service for this trend yet"
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {label}
    </span>
  );
}
