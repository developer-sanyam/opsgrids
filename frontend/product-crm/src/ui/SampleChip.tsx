import { cn } from "@/lib/cn";

/**
 * A small amber chip that marks a surface as representative rather than live — used on the
 * historical trend widgets while the analytics service is not yet wired. Amber (not the violet
 * accent, not an error red) reads as "informational, provisional".
 */
export function SampleChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late",
        className,
      )}
      title="Representative history — the pipeline analytics service isn't wired to this org yet"
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      Sample data
    </span>
  );
}
