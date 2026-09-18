import { useId, useMemo, useState } from "react";

import type { SpendPoint, SpendRange } from "@/api/types";
import { SPEND_DELTA, SPEND_TREND } from "@/api/fixtures";
import { DeltaPill, cn } from "@opsgrids/ui";
import { formatMoney, formatMoneyCompact } from "@/lib/format";

const RANGES: { value: SpendRange; label: string }[] = [
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

const W = 640;
const H = 200;
const PAD_Y = 16;

interface Geo {
  points: { x: number; y: number; p: SpendPoint }[];
  line: string;
  area: string;
}

function geometry(series: SpendPoint[]): Geo {
  const values = series.map((s) => s.spend_minor);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const step = W / (series.length - 1 || 1);
  const points = series.map((p, i) => {
    const x = i * step;
    const y = PAD_Y + (1 - (p.spend_minor - min) / span) * (H - PAD_Y * 2);
    return { x, y, p };
  });
  const line = points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.y}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  return { points, line, area };
}

/**
 * The wide spend-over-time card: a headline of committed spend for the period, a violet area+line
 * series, a range toggle and a hover tooltip. Spend is carried in minor units and formatted here.
 */
export function SpendTrend() {
  const [range, setRange] = useState<SpendRange>("monthly");
  const [hover, setHover] = useState<number | null>(null);
  const gradientId = useId();

  const series = SPEND_TREND[range];
  const geo = useMemo(() => geometry(series), [series]);
  const delta = SPEND_DELTA[range];
  const total = useMemo(
    () => series.reduce((sum, p) => sum + p.spend_minor, 0),
    [series],
  );

  const active = hover !== null ? geo.points[hover] : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <span className="display text-3xl font-extrabold tnum leading-none">
              {formatMoneyCompact(total)}
            </span>
            <DeltaPill value={delta} goodWhen="down" />
          </div>
          <span className="text-sm text-muted">committed spend · this {range.replace("ly", "")} window</span>
        </div>
        <div
          role="tablist"
          aria-label="Spend range"
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
                "rounded-full px-3 h-8 text-xs font-semibold transition-colors",
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

      <figure className="relative m-0">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-44 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Spend trend, ${range}`}
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
            <span className="tnum">{formatMoney(active.p.spend_minor)}</span>
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
          Spend {range}: {series.map((p) => `${p.label} ${formatMoney(p.spend_minor)}`).join(", ")}.
        </figcaption>
      </figure>
    </div>
  );
}
