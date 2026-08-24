import { formatMoneyCompact } from "@/lib/format";
import { cn } from "@/lib/cn";

interface BudgetBarProps {
  spentMinor: number;
  budgetMinor: number;
  currency: string;
  className?: string;
}

/**
 * Budget consumption: a track filled to the spent share of budget, with the compact spent / budget
 * figures beneath. At or over budget the fill turns negative-red so an overspend is unmistakable.
 */
export function BudgetBar({ spentMinor, budgetMinor, currency, className }: BudgetBarProps) {
  const ratio = budgetMinor === 0 ? 0 : spentMinor / budgetMinor;
  const pct = Math.round(ratio * 100);
  const over = ratio > 1.0001;
  const width = Math.min(Math.max(ratio, 0), 1) * 100;

  return (
    <div className={cn("flex flex-col gap-1", className)}>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-ground"
        role="img"
        aria-label={`${pct}% of budget spent`}
      >
        <div
          className={cn("h-full rounded-full", over ? "bg-negative" : "bg-accent")}
          style={{ width: `${Math.max(width, 2)}%` }}
        />
      </div>
      <div className="flex items-center justify-between text-xs">
        <span className="tnum font-semibold text-ink-soft">
          {formatMoneyCompact(spentMinor, currency)}
        </span>
        <span className="tnum text-faint">
          / {formatMoneyCompact(budgetMinor, currency)}
        </span>
      </div>
    </div>
  );
}
