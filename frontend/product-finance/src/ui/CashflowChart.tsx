import { useId } from "react";

import type { CashflowPoint } from "@/api/types";
import { formatMoneyCompact } from "@/lib/money";

interface CashflowChartProps {
  data: CashflowPoint[];
}

const W = 640;
const H = 240;
const PAD_X = 16;
const PAD_TOP = 16;
const PAD_BOTTOM = 34;

function buildPath(
  values: number[],
  max: number,
  { area }: { area: boolean },
): string {
  const innerW = W - PAD_X * 2;
  const innerH = H - PAD_TOP - PAD_BOTTOM;
  const step = innerW / (values.length - 1 || 1);
  const points = values.map((v, i) => {
    const x = PAD_X + i * step;
    const y = PAD_TOP + innerH - (max === 0 ? 0 : (v / max) * innerH);
    return [x, y] as const;
  });
  const line = points
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(1)}`)
    .join(" ");
  if (!area) return line;
  const baseline = PAD_TOP + innerH;
  const first = points[0]!;
  const last = points[points.length - 1]!;
  return `${line} L${last[0].toFixed(1)} ${baseline} L${first[0].toFixed(1)} ${baseline} Z`;
}

/**
 * Cash in vs cash out over the last few months. Two layered SVG areas — inflow in the violet
 * accent, outflow in the danger red — over a shared scale, with month labels and a legend that
 * names the latest figure for each series. The underlying numbers are also exposed as an
 * off-screen table so a screen reader gets the same data the chart shows.
 */
export function CashflowChart({ data }: CashflowChartProps) {
  const inflowFill = useId();
  const outflowFill = useId();

  const inflow = data.map((d) => d.inflow_minor);
  const outflow = data.map((d) => d.outflow_minor);
  const max = Math.max(...inflow, ...outflow, 1);

  const innerW = W - PAD_X * 2;
  const step = innerW / (data.length - 1 || 1);
  const latest = data[data.length - 1];

  return (
    <figure className="flex flex-col gap-3 m-0">
      <figcaption className="flex items-center gap-4 text-xs font-semibold">
        <span className="inline-flex items-center gap-1.5 text-ink-soft">
          <span className="size-2.5 rounded-full bg-accent" aria-hidden />
          Inflow
          {latest ? (
            <span className="money text-faint">{formatMoneyCompact(latest.inflow_minor)}</span>
          ) : null}
        </span>
        <span className="inline-flex items-center gap-1.5 text-ink-soft">
          <span className="size-2.5 rounded-full bg-danger" aria-hidden />
          Outflow
          {latest ? (
            <span className="money text-faint">{formatMoneyCompact(latest.outflow_minor)}</span>
          ) : null}
        </span>
      </figcaption>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        role="img"
        aria-label="Monthly cash inflow versus outflow"
        preserveAspectRatio="none"
      >
        <defs>
          <linearGradient id={inflowFill} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-accent)" stopOpacity="0.28" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0.02" />
          </linearGradient>
          <linearGradient id={outflowFill} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--color-danger)" stopOpacity="0.20" />
            <stop offset="100%" stopColor="var(--color-danger)" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        <path d={buildPath(outflow, max, { area: true })} fill={`url(#${outflowFill})`} />
        <path
          d={buildPath(outflow, max, { area: false })}
          fill="none"
          stroke="var(--color-danger)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path d={buildPath(inflow, max, { area: true })} fill={`url(#${inflowFill})`} />
        <path
          d={buildPath(inflow, max, { area: false })}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth={2.4}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {data.map((d, i) => (
          <text
            key={d.label}
            x={PAD_X + i * step}
            y={H - 12}
            textAnchor="middle"
            className="fill-faint"
            style={{ fontSize: "13px", fontWeight: 600 }}
          >
            {d.label}
          </text>
        ))}
      </svg>

      <table className="sr-only">
        <caption>Monthly cash inflow and outflow</caption>
        <thead>
          <tr>
            <th scope="col">Month</th>
            <th scope="col">Inflow</th>
            <th scope="col">Outflow</th>
          </tr>
        </thead>
        <tbody>
          {data.map((d) => (
            <tr key={d.label}>
              <th scope="row">{d.label}</th>
              <td>{formatMoneyCompact(d.inflow_minor)}</td>
              <td>{formatMoneyCompact(d.outflow_minor)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
