import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

interface DeltaPillProps {
  value: number;
  /**
   * When true, a *decrease* is the good outcome (e.g. breaching tickets, first-response time):
   * colour reflects good/bad while the arrow still points in the real direction. The number and
   * arrow always carry the direction, so the meaning never rests on colour alone.
   */
  invert?: boolean;
  className?: string;
}

/**
 * A change indicator: an up/down arrow for the real direction, and a green/red fill for whether
 * that direction is good. By default up is good; `invert` flips the colour for down-is-better KPIs.
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
