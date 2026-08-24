import { useId, useMemo, useState } from "react";

import type { BurndownPoint, BurndownRange } from "@/api/types";
import { BURNDOWN, BURNDOWN_DELTA } from "@/api/fixtures";
import { cn } from "@/lib/cn";
import { DeltaPill } from "./DeltaPill";

const RANGES: { value: BurndownRange; label: string }[] = [
  { value: "sprint", label: "Sprint" },
  { value: "release", label: "Release" },
];

const W = 640;
const H = 210;
const PAD_Y = 16;

interface Node {
  x: number;
  idealY: number;
  actualY: number | null;
  p: BurndownPoint;
}

interface Geo {
  nodes: Node[];
  idealPath: string;
  actualPath: string;
  actualArea: string;
}

function geometry(series: BurndownPoint[]): Geo {
  const max = Math.max(...series.map((s) => Math.max(s.ideal, s.actual ?? 0)));
  const span = max || 1;
  const step = W / (series.length - 1 || 1);
  const y = (v: number) => PAD_Y + (1 - v / span) * (H - PAD_Y * 2);

  const nodes: Node[] = series.map((p, i) => ({
    x: i * step,
    idealY: y(p.ideal),
    actualY: p.actual === null ? null : y(p.actual),
    p,
  }));

  const idealPath = nodes
    .map((n, i) => `${i === 0 ? "M" : "L"}${n.x},${n.idealY}`)
    .join(" ");

  const actualNodes = nodes.filter((n): n is Node & { actualY: number } => n.actualY !== null);
  const actualPath = actualNodes
    .map((n, i) => `${i === 0 ? "M" : "L"}${n.x},${n.actualY}`)
    .join(" ");
  const lastActual = actualNodes[actualNodes.length - 1];
  const actualArea = lastActual
    ? `${actualPath} L${lastActual.x},${H} L0,${H} Z`
    : "";

  return { nodes, idealPath, actualPath, actualArea };
}

/**
 * A release / sprint burndown: remaining scope over time, with a straight "ideal" guide line and
 * the "actual" line trailing it. The headline is the live remaining figure; hovering reveals the
 * ideal-vs-actual gap at each point. Fewer points remaining is the good direction, so the delta
 * pill treats a fall as positive.
 */
export function Burndown() {
  const [range, setRange] = useState<BurndownRange>("release");
  const [hover, setHover] = useState<number | null>(null);
  const areaId = useId();

  const series = BURNDOWN[range];
  const geo = useMemo(() => geometry(series), [series]);
  const delta = BURNDOWN_DELTA[range];

  const remaining = useMemo(() => {
    const withActual = series.filter((s) => s.actual !== null);
    return withActual[withActual.length - 1]?.actual ?? 0;
  }, [series]);

  const active = hover !== null ? geo.nodes[hover] : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2.5">
            <span className="display text-3xl font-extrabold tnum leading-none">
              {remaining}
            </span>
            <span className="text-sm font-medium text-muted">pts left</span>
            <DeltaPill value={delta} goodWhenDown />
          </div>
          <span className="text-sm text-muted">remaining scope · burndown vs plan</span>
        </div>
        <div
          role="tablist"
          aria-label="Burndown range"
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
                "rounded-full px-3.5 h-8 text-xs font-semibold transition-colors",
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
          className="w-full h-52 overflow-visible"
          preserveAspectRatio="none"
          role="img"
          aria-label={`Burndown, ${range}: remaining scope against the ideal plan`}
          onPointerMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const frac = (e.clientX - rect.left) / rect.width;
            const idx = Math.round(frac * (series.length - 1));
            setHover(Math.min(Math.max(idx, 0), series.length - 1));
          }}
          onPointerLeave={() => setHover(null)}
        >
          <defs>
            <linearGradient id={areaId} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.24" />
              <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
            </linearGradient>
          </defs>

          {geo.actualArea ? <path d={geo.actualArea} fill={`url(#${areaId})`} /> : null}

          {/* Ideal guide — dashed, muted. */}
          <path
            d={geo.idealPath}
            fill="none"
            stroke="var(--color-faint)"
            strokeWidth={1.75}
            strokeDasharray="5 5"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          {/* Actual — the accent line. */}
          <path
            d={geo.actualPath}
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
              {active.actualY !== null ? (
                <>
                  <circle cx={active.x} cy={active.actualY} r={4} fill="var(--color-accent)" />
                  <circle cx={active.x} cy={active.actualY} r={7} fill="var(--color-accent)" opacity={0.18} />
                </>
              ) : (
                <circle cx={active.x} cy={active.idealY} r={3.5} fill="var(--color-faint)" />
              )}
            </g>
          ) : null}
        </svg>

        {active ? (
          <div
            className="pointer-events-none absolute -translate-x-1/2 -translate-y-full rounded-tile bg-ink px-2.5 py-1.5 text-xs font-semibold text-surface shadow-float"
            style={{
              left: `${(active.x / W) * 100}%`,
              top: `${((active.actualY ?? active.idealY) / H) * 100}%`,
            }}
          >
            <span className="block text-surface/60">{active.p.label}</span>
            <span className="tnum">
              {active.p.actual === null ? "planned" : `${active.p.actual} left`} · plan {active.p.ideal}
            </span>
          </div>
        ) : null}

        <div className="mt-2 flex justify-between px-0.5 text-[0.7rem] font-medium text-faint">
          {series.map((p, i) => (
            <span key={`${p.label}-${i}`} className="tnum">
              {p.label}
            </span>
          ))}
        </div>
        <figcaption className="sr-only">
          Burndown {range}:{" "}
          {series
            .map((p) => `${p.label} plan ${p.ideal}${p.actual === null ? "" : `, actual ${p.actual}`}`)
            .join("; ")}
          .
        </figcaption>
      </figure>

      <div className="flex items-center gap-5 border-t border-line pt-3 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-5 rounded-full bg-accent" aria-hidden />
          Actual
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-0 w-5 border-t-2 border-dashed border-faint"
            aria-hidden
          />
          Ideal
        </span>
      </div>
    </div>
  );
}
