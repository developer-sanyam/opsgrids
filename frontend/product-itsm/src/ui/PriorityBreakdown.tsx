import type { Breakdown } from "@/api/types";

/** Priority-semantic colours: P1 danger, P2 amber, P3 blue, P4 grey. Keyed by the P-code. */
const PRIORITY_COLOR: Record<string, string> = {
  P1: "var(--color-danger)",
  P2: "var(--color-late)",
  P3: "var(--color-info)",
  P4: "var(--color-faint)",
};

interface PriorityBreakdownProps {
  data: Breakdown[];
  total: number;
}

/**
 * Open incidents grouped by priority as horizontal bars: a coloured dot, the band, the count and
 * its share, with a track showing the proportion. Derived live from the incident list.
 */
export function PriorityBreakdown({ data, total }: PriorityBreakdownProps) {
  const top = data[0];
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3.5">
        {data.map((band) => {
          const color = PRIORITY_COLOR[band.key] ?? "var(--color-accent)";
          return (
            <li key={band.key} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-[2px]"
                  style={{ backgroundColor: color }}
                  aria-hidden
                />
                <span className="font-semibold text-ink">{band.label}</span>
                <span className="ml-auto tnum font-semibold text-ink">{band.count}</span>
                <span className="w-10 text-right tnum text-muted">{band.pct}%</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full"
                  style={{ width: `${Math.max(band.pct, 3)}%`, backgroundColor: color }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      {top ? (
        <p className="border-t border-line pt-3 text-sm text-muted">
          Most open at <span className="font-semibold text-ink">{top.label}</span>
          {" · "}
          <span className="tnum font-semibold text-ink">{top.count}</span> of{" "}
          <span className="tnum">{total}</span> open
        </p>
      ) : null}
    </div>
  );
}
