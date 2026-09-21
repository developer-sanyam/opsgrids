import { useId, useMemo, useState } from "react";

import type { TrendPoint } from "@/api/types";
import { DeltaPill, SampleChip } from "@opsgrids/ui";

const W = 640;
const H = 200;
const PAD_Y = 16;

interface Geo {
  points: { x: number; y: number; p: TrendPoint }[];
  line: string;
  area: string;
}

function geometry(series: TrendPoint[]): Geo {
  const values = series.map((s) => s.value);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const step = W / (series.length - 1 || 1);
  const points = series.map((p, i) => {
    const x = i * step;
    const y = PAD_Y + (1 - (p.value - min) / span) * (H - PAD_Y * 2);
    return { x, y, p };
  });
  const line = points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.y}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  return { points, line, area };
}

interface UtilizationTrendProps {
  /** The live current-week utilization shown as the headline. */
  current: number;
  /** Omit where there is no real prior-week figure to compare against — no delta pill renders,
   * rather than a fabricated "+0%" that claims a week-over-week comparison that was never made. */
  deltaPct?: number;
  series: TrendPoint[];
  /** Marks the series (not the live headline) as representative — `/timesheets/summary`'s
   * history is a server-side constant until real weeks accumulate; only its last point is live. */
  sampleHistory?: boolean;
}

/**
 * The wide utilization-over-time card: a live headline percentage with a filled area+line series
 * across the trailing weeks, a hover tooltip, and a subtle target line at 80% capacity.
 */
export function UtilizationTrend({ current, deltaPct, series, sampleHistory = false }: UtilizationTrendProps) {
  const [hover, setHover] = useState<number | null>(null);
  const gradientId = useId();

  const geo = useMemo(() => geometry(series), [series]);
  const active = hover !== null ? geo.points[hover] : null;

  // The 80% target as a fraction of the chart's value span, drawn as a dashed guide.
  const values = series.map((s) => s.value);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const targetY =
    80 >= min && 80 <= max ? PAD_Y + (1 - (80 - min) / span) * (H - PAD_Y * 2) : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <span className="display text-3xl font-extrabold tnum leading-none">{current}%</span>
            {deltaPct !== undefined ? <DeltaPill value={deltaPct} /> : null}
            {sampleHistory ? <SampleChip label="Sample trend" title="The current week's figure is live; the prior weeks shown are representative until real history accumulates." /> : null}
          </div>
          <span className="text-sm text-muted">team utilization this week · trailing weeks</span>
        </div>
        <div className="flex items-center gap-3 text-xs font-medium text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-4 rounded-full bg-accent" aria-hidden />
            Utilization
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-0 w-4 border-t-2 border-dashed border-line" aria-hidden />
            80% target
          </span>
        </div>
      </div>

      <figure className="relative m-0">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-44 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label="Team utilization over the trailing twelve weeks"
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const frac = (e.clientX - rect.left) / rect.width;
            const idx = Math.round(frac * (series.length - 1));
            setHover(Math.min(Math.max(idx, 0), series.length - 1));
          }}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.28" />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          {targetY !== null ? (
            <line
              x1={0}
              y1={targetY}
              x2={W}
              y2={targetY}
              stroke="var(--color-line)"
              strokeWidth={1.5}
              strokeDasharray="4 5"
              vectorEffect="non-scaling-stroke"
            />
          ) : null}
          <path d={geo.area} fill={`url(#${gradientId})`} />
          <path
            d={geo.line}
            fill="none"
            stroke="var(--color-accent)"
            strokeWidth={2.5}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
          {active ? (
            <g>
              <line
                x1={active.x}
                y1={0}
                x2={active.x}
                y2={H}
                stroke="var(--color-line)"
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
              />
              <circle cx={active.x} cy={active.y} r={4} fill="var(--color-accent)" />
              <circle cx={active.x} cy={active.y} r={7} fill="var(--color-accent)" opacity={0.18} />
            </g>
          ) : null}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-tile bg-ink px-2.5 py-1.5 text-xs font-semibold text-surface shadow-float"
            style={{
              left: `${(active.x / W) * 100}%`,
              top: `${(active.y / H) * 100}%`,
            }}
          >
            <span className="block text-surface/60">{active.p.label}</span>
            <span className="tnum">{active.p.value}% utilized</span>
          </div>
        ) : null}

        <div className="mt-2 flex justify-between px-0.5 text-[0.7rem] font-medium text-faint">
          {series.map((p) => (
            <span key={p.label} className="tnum">
              {p.label}
            </span>
          ))}
        </div>
        <figcaption className="sr-only">
          Utilization by week: {series.map((p) => `${p.label} ${p.value}%`).join(", ")}.
        </figcaption>
      </figure>
    </div>
  );
}
