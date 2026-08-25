import { cn } from "../lib/cn";

interface SampleChipProps {
  className?: string;
  /**
   * What is representative. Defaults to "Sample data" — the whole widget. Pass "Sample trend"
   * where the headline number is live and only its delta and sparkline are stand-ins.
   */
  label?: string;
  /** The hover explanation. Says which service is not wired yet, in this product's words. */
  title?: string;
}

/**
 * A small amber chip marking a surface as representative rather than live, for the widgets whose
 * backing service is not wired yet. Amber — not the violet accent, not an error red — reads as
 * "informational, provisional".
 */
export function SampleChip({
  className,
  label = "Sample data",
  title = "Representative data — the service behind this isn't wired to this org yet",
}: SampleChipProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late",
        className,
      )}
      title={title}
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      {label}
    </span>
  );
}
