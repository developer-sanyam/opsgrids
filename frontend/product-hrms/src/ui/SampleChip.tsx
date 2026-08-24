import { cn } from "@/lib/cn";

/**
 * A small amber chip that marks a surface as representative rather than live — used on the
 * attendance widgets while the attendance service is not yet wired. Amber (not the accent mint,
 * not an error red) reads as "informational, provisional".
 */
export function SampleChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late",
        className,
      )}
      title="Representative data — the attendance service isn't wired to this org yet"
    >
      <span className="size-1.5 rounded-full bg-current" aria-hidden />
      Sample data
    </span>
  );
}
