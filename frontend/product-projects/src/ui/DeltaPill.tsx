import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

interface DeltaPillProps {
  value: number;
  /** When a fall is the good outcome (e.g. burndown), invert the colour mapping. */
  goodWhenDown?: boolean;
  className?: string;
}

/**
 * A change indicator: green pill with an up arrow for a rise, red with a down arrow for a fall.
 * The arrow and the sign both carry the direction, so it never rests on colour alone. Pass
 * `goodWhenDown` for metrics where less is better and a fall should read positive.
 */
export function DeltaPill({ value, goodWhenDown = false, className }: DeltaPillProps) {
  const rising = value >= 0;
  const good = goodWhenDown ? !rising : rising;
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
