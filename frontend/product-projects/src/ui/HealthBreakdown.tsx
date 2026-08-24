import type { HealthSlice } from "@/api/types";
import { healthColor } from "./healthColor";

interface HealthBreakdownProps {
  slices: HealthSlice[];
  total: number;
}

const R = 46;
const C = 2 * Math.PI * R;
const SIZE = 116;
const MID = SIZE / 2;

/**
 * The delivery-health breakdown: a donut split into on-track / at-risk / delayed with the
 * portfolio total in the centre, and a legend carrying each health's count and share. Derived
 * live from the portfolio, coloured strictly from the semantic (not accent) palette.
 */
export function HealthBreakdown({ slices, total }: HealthBreakdownProps) {
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
            {slices.map((slice) => {
              const len = (slice.pct / 100) * C;
              const dash = `${len} ${C - len}`;
              const el = (
                <circle
                  key={slice.key}
                  cx={MID}
                  cy={MID}
                  r={R}
                  fill="none"
                  stroke={healthColor(slice.key)}
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
            <span className="text-[0.7rem] font-medium text-muted">Projects</span>
          </div>
        </div>

        <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
          {slices.map((slice) => (
            <li key={slice.key} className="flex items-center gap-2 text-sm">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: healthColor(slice.key) }}
                aria-hidden
              />
              <span className="whitespace-nowrap text-ink-soft">{slice.label}</span>
              <span className="ml-auto tnum font-semibold text-ink">{slice.count}</span>
              <span className="w-10 text-right tnum text-muted">{slice.pct}%</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
