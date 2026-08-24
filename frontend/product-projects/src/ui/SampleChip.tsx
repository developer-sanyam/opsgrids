import { cn } from "@/lib/cn";

/**
 * A small amber chip that marks a surface as representative rather than live. Amber (not the
 * violet accent, not an error red) reads as "informational, provisional" — used where a widget
 * shows a modelled figure the backing service does not yet compute live.
 */
export function SampleChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late",
        className,
      )}
      title="Representative data — this metric's service isn't wired to this workspace yet"
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      Sample data
    </span>
  );
}
