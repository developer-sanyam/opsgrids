import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

interface DeltaPillProps {
  value: number;
  /**
   * Whether a rise is good. Defaults to true (green = up). For a metric where lower is better —
   * open incidents, breaches — set `higherIsBetter={false}` so a fall reads green.
   */
  higherIsBetter?: boolean;
  className?: string;
}

/**
 * A change indicator: the arrow and sign always follow the numeric direction, while the colour
 * follows whether that direction is *good* for this metric. Never rests on colour alone.
 */
export function DeltaPill({ value, higherIsBetter = true, className }: DeltaPillProps) {
  const rising = value >= 0;
  const good = rising === higherIsBetter;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-0.5 rounded-full pl-1 pr-2 py-0.5 text-xs font-bold tnum",
        good ? "bg-positive-soft text-positive" : "bg-negative-soft text-negative",
        className,
      )}
    >
      {rising ? (
        <ArrowUp className="size-3.5" strokeWidth={2.4} />
      ) : (
        <ArrowDown className="size-3.5" strokeWidth={2.4} />
      )}
      {formatDelta(value).replace(/^[+−]/, "")}
    </span>
  );
}
