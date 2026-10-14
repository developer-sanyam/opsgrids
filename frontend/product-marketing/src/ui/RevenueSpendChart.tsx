import { useId, useMemo, useState } from "react";

import type { RevenueSpendPoint } from "@/api/types";
import { formatMoneyCompact, formatRoas } from "@/lib/format";

const W = 640;
const H = 210;
const PAD_Y = 18;

interface PlotPoint {
  x: number;
  yRev: number;
  ySpend: number;
  p: RevenueSpendPoint;
}

interface Geo {
  points: PlotPoint[];
  revLine: string;
  revArea: string;
  spendLine: string;
}

function geometry(series: RevenueSpendPoint[]): Geo {
  const max = Math.max(1, ...series.map((s) => Math.max(s.revenue_minor, s.spend_minor)));
  const step = W / (series.length - 1 || 1);
  const y = (v: number): number => PAD_Y + (1 - v / max) * (H - PAD_Y * 2);
  const points = series.map((p, i) => ({
    x: i * step,
    yRev: y(p.revenue_minor),
    ySpend: y(p.spend_minor),
    p,
  }));
  const revLine = points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.yRev}`).join(" ");
  const revArea = `${revLine} L${W},${H} L0,${H} Z`;
  const spendLine = points
    .map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.ySpend}`)
    .join(" ");
  return { points, revLine, revArea, spendLine };
}

interface RevenueSpendChartProps {
  series: RevenueSpendPoint[];
  currency: string;
}

/**
 * The Overview hero: attributed revenue as a violet area+line against spend as a quieter line, on
 * one shared scale so the gap between what marketing spends and what it returns is the headline.
 * Hovering reads out both figures and the month's blended ROAS.
 */
export function RevenueSpendChart({ series, currency }: RevenueSpendChartProps) {
  const [hover, setHover] = useState<number | null>(null);
  const gradientId = useId();
  const geo = useMemo(() => geometry(series), [series]);

  const active = hover !== null ? geo.points[hover] : null;
  const activeRoas =
    active && active.p.spend_minor > 0 ? active.p.revenue_minor / active.p.spend_minor : 0;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        <LegendSwatch color="var(--color-accent)" label="Revenue attributed" />
        <LegendSwatch color="var(--color-ink-soft)" label="Spend" dashed />
      </div>

      <figure className="relative m-0">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-52 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label="Attributed revenue against spend, monthly"
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
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.26" />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={geo.revArea} fill={`url(#${gradientId})`} />
          <path
            d={geo.spendLine}
            fill="none"
            stroke="var(--color-ink-soft)"
            strokeWidth={2}
            strokeDasharray="5 5"
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={geo.revLine}
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
              <circle cx={active.x} cy={active.ySpend} r={3.5} fill="var(--color-ink-soft)" />
              <circle cx={active.x} cy={active.yRev} r={4} fill="var(--color-accent)" />
              <circle cx={active.x} cy={active.yRev} r={7} fill="var(--color-accent)" opacity={0.18} />
            </g>
          ) : null}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-tile bg-ink px-3 py-2 text-xs font-semibold text-surface shadow-float"
            style={{ left: `${(active.x / W) * 100}%`, top: `${(active.yRev / H) * 100}%` }}
          >
            <span className="mb-1 block text-surface/60">{active.p.label} 2026</span>
            <span className="flex items-center justify-between gap-4 tnum">
              <span className="text-surface/70">Revenue</span>
              {formatMoneyCompact(active.p.revenue_minor, currency)}
            </span>
            <span className="flex items-center justify-between gap-4 tnum">
              <span className="text-surface/70">Spend</span>
              {formatMoneyCompact(active.p.spend_minor, currency)}
            </span>
            <span className="mt-1 flex items-center justify-between gap-4 border-t border-surface/15 pt-1 tnum">
              <span className="text-surface/70">ROAS</span>
              {formatRoas(activeRoas)}
            </span>
          </div>
        ) : null}

        <div className="mt-2 flex justify-between px-0.5 text-xs font-medium text-faint">
          {series.map((p) => (
            <span key={p.label} className="tnum">
              {p.label}
            </span>
          ))}
        </div>
        <figcaption className="sr-only">
          Monthly attributed revenue and spend:{" "}
          {series
            .map(
              (p) =>
                `${p.label} revenue ${formatMoneyCompact(p.revenue_minor, currency)}, spend ${formatMoneyCompact(p.spend_minor, currency)}`,
            )
            .join("; ")}
          .
        </figcaption>
      </figure>
    </div>
  );
}

function LegendSwatch({
  color,
  label,
  dashed = false,
}: {
  color: string;
  label: string;
  dashed?: boolean;
}) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-ink-soft">
      <svg width="20" height="8" aria-hidden className="shrink-0">
        <line
          x1="0"
          y1="4"
          x2="20"
          y2="4"
          stroke={color}
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeDasharray={dashed ? "4 4" : undefined}
        />
      </svg>
      {label}
    </span>
  );
}
