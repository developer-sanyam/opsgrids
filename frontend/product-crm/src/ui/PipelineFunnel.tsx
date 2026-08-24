import type { StageRollup } from "@/api/types";
import { stageMeta } from "@/data/stages";
import { formatMoneyCompact } from "@/lib/format";

interface PipelineFunnelProps {
  rollups: StageRollup[];
  currency: string;
}

/**
 * The open pipeline as a funnel: one row per open stage, a stage-coloured bar whose width tracks
 * that stage's share of the richest stage's value, with the deal count and summed value. Reads
 * top-to-bottom from earliest to latest stage.
 */
export function PipelineFunnel({ rollups, currency }: PipelineFunnelProps) {
  const totalCount = rollups.reduce((sum, r) => sum + r.count, 0);
  const totalValue = rollups.reduce((sum, r) => sum + r.value_minor, 0);

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-3">
        {rollups.map((row) => {
          const color = stageMeta(row.stage).color;
          return (
            <li key={row.stage} className="flex flex-col gap-1.5">
              <div className="flex items-center gap-2 text-sm">
                <span
                  className="size-2 shrink-0 rounded-full"
                  style={{ backgroundColor: color }}
                  aria-hidden
                />
                <span className="font-semibold text-ink">{row.label}</span>
                <span className="tnum text-xs text-muted">
                  {row.count} {row.count === 1 ? "deal" : "deals"}
                </span>
                <span className="ml-auto tnum font-semibold text-ink">
                  {formatMoneyCompact(row.value_minor, currency)}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-ground">
                <div
                  className="h-full rounded-full transition-[width] duration-500"
                  style={{ width: `${Math.max(row.pct, 3)}%`, backgroundColor: color }}
                />
              </div>
            </li>
          );
        })}
      </ul>

      <div className="flex items-center justify-between border-t border-line pt-3 text-sm">
        <span className="text-muted">
          <span className="tnum font-semibold text-ink">{totalCount}</span> open deals
        </span>
        <span className="text-muted">
          Open value{" "}
          <span className="tnum font-semibold text-ink">
            {formatMoneyCompact(totalValue, currency)}
          </span>
        </span>
      </div>
    </div>
  );
}
