import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

interface DeltaPillProps {
  value: number;
  /**
   * When true, a fall is the good outcome (e.g. spend, overdue count) so the tones invert:
   * a negative value reads green. The arrow always shows the literal direction of change.
   */
  invert?: boolean;
  className?: string;
}

/**
 * A change indicator: a coloured pill with a direction arrow. The arrow and the sign both carry
 * the direction, so meaning never rests on colour alone. `invert` flips which direction is
 * "good" for metrics where less is better.
 */
export function DeltaPill({ value, invert = false, className }: DeltaPillProps) {
  const rising = value >= 0;
  const good = invert ? !rising : rising;
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
