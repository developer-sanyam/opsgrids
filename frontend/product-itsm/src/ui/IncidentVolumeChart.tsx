import { useId, useMemo, useState } from "react";

import type { TrendPoint, TrendRange } from "@/api/types";
import { INCIDENT_VOLUME, VOLUME_DELTA } from "@/data/samples";
import { cn } from "@/lib/cn";
import { DeltaPill } from "./DeltaPill";
import { SampleChip } from "./SampleChip";

const RANGES: { value: TrendRange; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
];

const W = 640;
const H = 200;
const PAD_Y = 16;

interface Line {
  points: { x: number; y: number }[];
  path: string;
  area: string;
}

interface Geo {
  opened: Line;
  resolved: Line;
  xs: number[];
}

function toLine(values: number[], min: number, span: number, step: number): Line {
  const points = values.map((v, i) => ({
    x: i * step,
    y: PAD_Y + (1 - (v - min) / span) * (H - PAD_Y * 2),
  }));
  const path = points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.y}`).join(" ");
  const area = `${path} L${W},${H} L0,${H} Z`;
  return { points, path, area };
}

function geometry(series: TrendPoint[]): Geo {
  const all = series.flatMap((s) => [s.opened, s.resolved]);
  const max = Math.max(...all);
  const min = Math.min(...all);
  const span = max - min || 1;
  const step = W / (series.length - 1 || 1);
  return {
    opened: toLine(series.map((s) => s.opened), min, span, step),
    resolved: toLine(series.map((s) => s.resolved), min, span, step),
    xs: series.map((_, i) => i * step),
  };
}

interface IncidentVolumeChartProps {
  sample: boolean;
}

/**
 * Incident volume over time: two overlaid series — opened (violet, filled) and resolved (cyan
 * line) — with a range toggle and a hover tooltip. The history is representative until the
 * reporting service ships, so it is chipped when running live.
 */
export function IncidentVolumeChart({ sample }: IncidentVolumeChartProps) {
  const [range, setRange] = useState<TrendRange>("weekly");
  const [hover, setHover] = useState<number | null>(null);
  const openedGradient = useId();

  const series = INCIDENT_VOLUME[range];
  const geo = useMemo(() => geometry(series), [series]);
  const delta = VOLUME_DELTA[range];

  const totalOpened = series.reduce((sum, p) => sum + p.opened, 0);
  const active =
    hover !== null
      ? (() => {
          const p = series[hover];
          const x = geo.xs[hover];
          const openedY = geo.opened.points[hover]?.y;
          const resolvedY = geo.resolved.points[hover]?.y;
          if (p === undefined || x === undefined || openedY === undefined || resolvedY === undefined) {
            return null;
          }
          return { p, x, openedY, resolvedY };
        })()
      : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <span className="display text-3xl font-extrabold tnum leading-none">
              {totalOpened}
            </span>
            <DeltaPill value={delta} higherIsBetter={false} />
          </div>
          <span className="text-sm text-muted">incidents opened this period · vs last</span>
        </div>
        <div className="flex items-center gap-2">
          {sample ? <SampleChip /> : null}
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
      </div>

      {/* Legend */}
      <div className="flex items-center gap-4 text-xs font-semibold text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-accent" aria-hidden />
          Opened
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-cyan" aria-hidden />
          Resolved
        </span>
      </div>

      <figure className="relative m-0">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          className="w-full h-44 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Incident volume, ${range}`}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const frac = (e.clientX - rect.left) / rect.width;
            const idx = Math.round(frac * (series.length - 1));
            setHover(Math.min(Math.max(idx, 0), series.length - 1));
          }}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={openedGradient} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.26" />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>
          <path d={geo.opened.area} fill={`url(#${openedGradient})`} />
          <path
            d={geo.resolved.path}
            fill="none"
            stroke="var(--color-cyan)"
            strokeWidth={2}
            strokeDasharray="1 5"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          <path
            d={geo.opened.path}
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
              <circle cx={active.x} cy={active.openedY} r={4} fill="var(--color-accent)" />
              <circle cx={active.x} cy={active.resolvedY} r={4} fill="var(--color-cyan)" />
            </g>
          ) : null}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-tile bg-ink px-3 py-2 text-xs font-semibold text-surface shadow-float"
            style={{ left: `${(active.x / W) * 100}%`, top: "0%" }}
          >
            <span className="block text-surface/60">{active.p.label}</span>
            <span className="tnum">{active.p.opened} opened</span>
            <span className="tnum block text-surface/70">{active.p.resolved} resolved</span>
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
          Incident volume {range}:{" "}
          {series.map((p) => `${p.label} ${p.opened} opened ${p.resolved} resolved`).join(", ")}.
        </figcaption>
      </figure>
    </div>
  );
}
