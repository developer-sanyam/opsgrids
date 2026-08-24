import { Link } from "react-router-dom";

import type { WorkforceSlice } from "@/api/types";
import { ArrowRightIcon } from "./icons";

const PALETTE = [
  "var(--color-accent)",
  "var(--color-info)",
  "var(--color-cyan)",
  "var(--color-late)",
];

/** A small live fact for the mini-table below the donut. */
export interface MiniStat {
  label: string;
  metric: string;
  total: string;
}

interface WorkforceMixProps {
  slices: WorkforceSlice[];
  total: number;
  stats: MiniStat[];
}

const R = 46;
const C = 2 * Math.PI * R;
const SIZE = 116;
const MID = SIZE / 2;

/**
 * The workforce mix: a donut grouped live by employment type with the total in the centre, a
 * legend, and a compact live fact table plus a link out to the fuller report.
 */
export function WorkforceMix({ slices, total, stats }: WorkforceMixProps) {
  let offset = 0;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-5">
        <div className="relative shrink-0">
          <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden>
            <circle
              cx={MID}
              cy={MID}
              r={R}
              fill="none"
              stroke="var(--color-ground)"
              strokeWidth={12}
            />
            {slices.map((slice, i) => {
              const len = (slice.pct / 100) * C;
              const dash = `${len} ${C - len}`;
              const el = (
                <circle
                  key={slice.key}
                  cx={MID}
                  cy={MID}
                  r={R}
                  fill="none"
                  stroke={PALETTE[i % PALETTE.length]}
                  strokeWidth={12}
                  strokeDasharray={dash}
                  strokeDashoffset={-offset}
                  strokeLinecap="butt"
                  transform={`rotate(-90 ${MID} ${MID})`}
                />
              );
              offset += len;
              return el;
            })}
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className="display text-2xl font-extrabold tnum leading-none">{total}</span>
            <span className="text-[0.7rem] font-medium text-muted">Total staff</span>
          </div>
        </div>

        <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
          {slices.map((slice, i) => (
            <li key={slice.key} className="flex items-center gap-2 text-sm">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: PALETTE[i % PALETTE.length] }}
                aria-hidden
              />
              <span className="whitespace-nowrap text-ink-soft">{slice.label}</span>
              <span className="ml-auto tnum font-semibold text-ink">{slice.pct}%</span>
              <span className="w-6 text-right tnum text-muted">{slice.count}</span>
            </li>
          ))}
        </ul>
      </div>

      <table className="w-full border-t border-line text-sm">
        <caption className="sr-only">Workforce facts</caption>
        <thead>
          <tr className="text-xs font-semibold uppercase tracking-wide text-faint">
            <th scope="col" className="py-2 text-left font-semibold">Details</th>
            <th scope="col" className="py-2 text-right font-semibold">Metric</th>
            <th scope="col" className="py-2 text-right font-semibold">Total</th>
          </tr>
        </thead>
        <tbody>
          {stats.map((row) => (
            <tr key={row.label} className="border-t border-line/70">
              <td className="py-2 text-ink-soft">{row.label}</td>
              <td className="py-2 text-right tnum text-muted">{row.metric}</td>
              <td className="py-2 text-right tnum font-semibold text-ink">{row.total}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <Link
        to="/employees"
        className="inline-flex h-10 items-center justify-center gap-1.5 rounded-full border border-line bg-surface text-sm font-semibold text-ink hover:bg-ground transition-colors"
      >
        View reports
        <ArrowRightIcon className="size-4" />
      </Link>
    </div>
  );
}
