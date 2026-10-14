import { cn } from "@opsgrids/ui";

import { formatMoneyCompact } from "@/lib/format";

export interface WeekPoint {
  label: string;
  revenue_minor: number;
  spend_minor: number;
}

const W = 560;
const H = 200;
const BASE = H - 4;
const TOP = 10;

interface CampaignPerformanceChartProps {
  weeks: WeekPoint[];
  currency: string;
}

/**
 * A campaign's weekly revenue against spend as grouped bars — revenue in the violet accent, spend
 * in a quiet neutral. One shared scale keeps the two comparable; each bar carries a native tooltip
 * for its exact figure, and the whole series is described for screen readers.
 */
export function CampaignPerformanceChart({ weeks, currency }: CampaignPerformanceChartProps) {
  const max = Math.max(1, ...weeks.map((w) => Math.max(w.revenue_minor, w.spend_minor)));
  const groupW = W / (weeks.length || 1);
  const barW = Math.min(groupW * 0.3, 26);
  const height = (v: number): number => ((v / max) * (BASE - TOP));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <Swatch color="var(--color-accent)" label="Revenue" />
        <Swatch color="var(--color-ink-soft)" label="Spend" />
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-52"
        role="img"
        aria-label="Weekly revenue against spend for this campaign"
      >
        <line
          x1={0}
          y1={BASE}
          x2={W}
          y2={BASE}
          stroke="var(--color-line)"
          strokeWidth={1}
        />
        {weeks.map((w, i) => {
          const cx = i * groupW + groupW / 2;
          const revH = height(w.revenue_minor);
          const spendH = height(w.spend_minor);
          return (
            <g key={w.label}>
              <rect
                x={cx - barW - 2}
                y={BASE - revH}
                width={barW}
                height={Math.max(revH, 1)}
                rx={3}
                fill="var(--color-accent)"
              >
                <title>{`${w.label} revenue ${formatMoneyCompact(w.revenue_minor, currency)}`}</title>
              </rect>
              <rect
                x={cx + 2}
                y={BASE - spendH}
                width={barW}
                height={Math.max(spendH, 1)}
                rx={3}
                fill="var(--color-ink-soft)"
                opacity={0.55}
              >
                <title>{`${w.label} spend ${formatMoneyCompact(w.spend_minor, currency)}`}</title>
              </rect>
            </g>
          );
        })}
      </svg>
      {/* Week labels are text on the page, not in the drawing: an SVG scaled to a phone's width
          shrinks its own type to about 5px. With many weeks a phone shows every other label. */}
      <div className="flex" aria-hidden>
        {weeks.map((w, i) => (
          <span
            key={w.label}
            className={cn(
              "min-w-0 flex-1 text-center text-xs font-semibold text-faint tnum",
              weeks.length > 6 && i % 2 === 1 && "invisible sm:visible",
            )}
          >
            {w.label}
          </span>
        ))}
      </div>
    </div>
  );
}

function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-soft">
      <span
        className="size-3 rounded-[3px]"
        style={{ backgroundColor: color }}
        aria-hidden
      />
      {label}
    </span>
  );
}
