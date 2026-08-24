import type { BreakdownSlice } from "@/api/types";

interface CategoryBreakdownProps {
  slices: BreakdownSlice[];
  /** Maps each slice key to a CSS colour (a token var), so priority/status keep their own hues. */
  colors: Record<string, string>;
  /** What the counts are counting, for the footer summary (e.g. "in the queue"). */
  totalNoun: string;
}

/**
 * A categorical breakdown as labelled horizontal bars — used for tickets by priority and by
 * status. The label and the count carry the meaning; colour is a secondary cue keyed per category.
 */
export function CategoryBreakdown({ slices, colors, totalNoun }: CategoryBreakdownProps) {
  const total = slices.reduce((sum, s) => sum + s.count, 0);
  const fallback = "var(--color-muted)";
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3.5">
        {slices.map((slice) => {
          const color = colors[slice.key] ?? fallback;
          return (
            <li key={slice.key} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                  aria-hidden
                />
                <span className="font-semibold text-ink">{slice.label}</span>
                <span className="ml-auto tnum font-semibold text-ink">{slice.count}</span>
                <span className="w-10 text-right tnum text-muted">{slice.pct}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.max(slice.pct, 3)}%`, backgroundColor: color }}
                />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="border-t border-line pt-3 text-sm text-muted">
        <span className="tnum font-semibold text-ink">{total}</span> {totalNoun}
      </p>
    </div>
  );
}
