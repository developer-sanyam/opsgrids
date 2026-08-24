import type { CategorySpend } from "@/api/types";
import { formatMoneyCompact } from "@/lib/format";

/** A stable dot/bar colour per rank, drawn from the token palette. */
const PALETTE = [
  "var(--color-accent)",
  "var(--color-info)",
  "var(--color-cyan)",
  "var(--color-present)",
  "var(--color-late)",
  "var(--color-danger)",
];

interface CategoryBreakdownProps {
  data: CategorySpend[];
}

/**
 * Committed spend grouped by category as horizontal bars: a coloured dot, the name, the amount
 * and its share, with a track showing the proportion. Derived live from the purchase orders.
 */
export function CategoryBreakdown({ data }: CategoryBreakdownProps) {
  const largest = data[0];
  const grand = data.reduce((sum, d) => sum + d.spend_minor, 0);

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3.5">
        {data.map((cat, i) => {
          const color = PALETTE[i % PALETTE.length];
          return (
            <li key={cat.key} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                  aria-hidden
                />
                <span className="font-semibold text-ink">{cat.name}</span>
                <span className="ml-auto tnum font-semibold text-ink">
                  {formatMoneyCompact(cat.spend_minor)}
                </span>
                <span className="w-10 text-right tnum text-muted">{cat.pct}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.max(cat.pct, 3)}%`, backgroundColor: color }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      {largest ? (
        <p className="border-t border-line pt-3 text-sm text-muted">
          Top category{" "}
          <span className="font-semibold text-ink">{largest.name}</span>
          {" · "}
          <span className="tnum font-semibold text-ink">{largest.pct}%</span> of{" "}
          <span className="tnum">{formatMoneyCompact(grand)}</span> committed
        </p>
      ) : null}
    </div>
  );
}
