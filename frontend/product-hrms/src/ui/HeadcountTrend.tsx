import { useId, useMemo, useState } from "react";

import type { TrendPoint, TrendRange } from "@/api/types";
import { trendDeltaPct } from "@/data/derive";
import { DeltaPill, cn } from "@opsgrids/ui";

const RANGES: { value: TrendRange; label: string }[] = [
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

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

interface HeadcountTrendProps {
  /** The live current headcount shown as the headline. */
  current: number;
  /** Real headcount series per range, counted from each employee's own join/exit dates. */
  series: Record<TrendRange, TrendPoint[]>;
}

/**
 * The wide headcount-trend card: a live headline number with the real area+line series (derived
 * from every employee's `date_of_joining`/`date_of_exit`), a range toggle, and a hover tooltip.
 */
export function HeadcountTrend({ current, series: seriesByRange }: HeadcountTrendProps) {
  const [range, setRange] = useState<TrendRange>("monthly");
  const [hover, setHover] = useState<number | null>(null);
  const gradientId = useId();

  const series = seriesByRange[range];
  const geo = useMemo(() => geometry(series), [series]);
  const delta = useMemo(() => trendDeltaPct(series), [series]);

  const active = hover !== null ? geo.points[hover] : null;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <span className="display text-3xl font-extrabold tnum leading-none">
              {current}
            </span>
            {delta !== null ? <DeltaPill value={delta} /> : null}
          </div>
          <span className="text-sm text-muted">current headcount · vs last period</span>
        </div>
        <div className="flex items-center gap-2">
          <div
            role="tablist"
            aria-label="Trend range"
            className="flex items-center rounded-full bg-ground p-0.5"
          >
            {RANGES.map((r) => (
              <button
                key={r.value}
                type="button"
                role="tab"
                aria-selected={range === r.value}
                onClick={() => {
                  setRange(r.value);
                  setHover(null);
                }}
                className={cn(
                  "hit-44 relative h-8 rounded-full px-3 text-xs font-semibold transition-colors",
                  range === r.value
                    ? "bg-surface text-ink shadow-card"
                    : "text-muted hover:text-ink",
                )}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <figure className="relative m-0 min-w-0">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-44 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Headcount trend, ${range}`}
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
            <span className="tnum">{active.p.value} people</span>
          </div>
        ) : null}

        <div className="mt-2 flex justify-between px-0.5 text-xs font-medium text-faint">
          {/* Every other label on a phone: twelve month names do not fit in 280px without touching. */}
          {series.map((p, i) => (
            <span key={p.label} className={cn("tnum", i % 2 === 1 && "hidden sm:inline")}>
              {p.label}
            </span>
          ))}
        </div>
        <figcaption className="sr-only">
          Headcount {range}: {series.map((p) => `${p.label} ${p.value}`).join(", ")}.
        </figcaption>
      </figure>
    </div>
  );
}
