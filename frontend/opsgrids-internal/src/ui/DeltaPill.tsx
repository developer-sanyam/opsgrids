import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

interface DeltaPillProps {
  value: number;
  /**
   * Whether a rise is the good outcome (default true). For metrics like churn a *fall* is good, so
   * pass `false` and the tone follows the meaning, not the sign — but the arrow always shows the
   * real direction, so state never rests on colour alone.
   */
  positiveIsGood?: boolean;
  className?: string;
}

/**
 * A change indicator: an up/down arrow and the signed magnitude, tinted by whether the change is
 * good or bad. Both the arrow and the sign carry the direction independently of colour.
 */
export function DeltaPill({ value, positiveIsGood = true, className }: DeltaPillProps) {
  const rising = value >= 0;
  const good = rising === positiveIsGood;
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
