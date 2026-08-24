import { cn } from "@/lib/cn";
import { formatDelta } from "@/lib/format";
import { ArrowDown, ArrowUp } from "./icons";

/**
 * A change indicator. The arrow and the sign always reflect the *actual* direction of the change,
 * so it never rests on colour alone. Colour reflects whether that change is *good*: for most
 * metrics up is good (green), but for a cost metric like cost-per-lead a fall is the win, so pass
 * `goodWhen="down"` and a drop turns green while the arrow still points down.
 */
export function DeltaPill({
  value,
  goodWhen = "up",
  className,
}: {
  value: number;
  goodWhen?: "up" | "down";
  className?: string;
}) {
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
