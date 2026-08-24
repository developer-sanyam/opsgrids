import { useId, useMemo, useState } from "react";

import type { MrrPoint } from "@/api/types";
import { formatMoney } from "@/lib/money";

const W = 640;
const H = 200;
const PAD_Y = 16;

interface Geo {
  points: { x: number; y: number; p: MrrPoint }[];
  line: string;
  area: string;
}

function geometry(series: MrrPoint[]): Geo {
  const values = series.map((s) => s.mrr_minor);
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const step = W / (series.length - 1 || 1);
  const points = series.map((p, i) => {
    const x = i * step;
    const y = PAD_Y + (1 - (p.mrr_minor - min) / span) * (H - PAD_Y * 2);
    return { x, y, p };
  });
  const line = points.map((pt, i) => `${i === 0 ? "M" : "L"}${pt.x},${pt.y}`).join(" ");
  const area = `${line} L${W},${H} L0,${H} Z`;
  return { points, line, area };
}

/**
 * The MRR-over-time area chart. A live current headline sits above (rendered by the caller); this
 * draws the trailing-12-months area+line with a hover tooltip that reads out the month's MRR. The
 * series carries integer minor amounts, so the tooltip formats real money.
 */
export function MrrTrend({ series, currency }: { series: MrrPoint[]; currency: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const gradientId = useId();
  const geo = useMemo(() => geometry(series), [series]);
  const active = hover !== null ? geo.points[hover] : null;

  return (
    <figure className="relative m-0">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-48 w-full overflow-visible"
        preserveAspectRatio="none"
        role="img"
        aria-label="Monthly recurring revenue, trailing twelve months"
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
          style={{ left: `${(active.x / W) * 100}%`, top: `${(active.y / H) * 100}%` }}
        >
          <span className="block text-surface/60">{active.p.label}</span>
          <span className="mono">{formatMoney(active.p.mrr_minor, currency)}</span>
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
        Monthly recurring revenue, trailing twelve months:{" "}
        {series.map((p) => `${p.label} ${formatMoney(p.mrr_minor, currency)}`).join(", ")}.
      </figcaption>
    </figure>
  );
}
