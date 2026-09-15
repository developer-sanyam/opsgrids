import { cn } from "../lib/cn";
import { formatDelta } from "../lib/format";
import { ArrowDown, ArrowUp } from "../icons";

interface DeltaPillProps {
  value: number;
  /**
   * Which direction is the good one for this metric. Defaults to `"up"`. Pass `"down"` for a
   * metric where less is better — cost per lead, open incidents, overdue balance — and a fall
   * turns green while the arrow still points down.
   */
  goodWhen?: "up" | "down";
  className?: string;
}

/**
 * A change indicator. The arrow and the sign always follow the *numeric* direction, so meaning
 * never rests on colour alone; the colour follows whether that direction is *good* here.
 */
export function DeltaPill({ value, goodWhen = "up", className }: DeltaPillProps) {
  const rising = value >= 0;
  const good = rising === (goodWhen === "up");
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
