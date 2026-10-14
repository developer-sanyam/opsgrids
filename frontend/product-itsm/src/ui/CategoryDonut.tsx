import type { Breakdown } from "@/api/types";

const PALETTE = [
  "var(--color-accent)",
  "var(--color-info)",
  "var(--color-cyan)",
  "var(--color-present)",
  "var(--color-late)",
  "var(--color-danger)",
];

interface CategoryDonutProps {
  data: Breakdown[];
  total: number;
}

const R = 46;
const C = 2 * Math.PI * R;
const SIZE = 116;
const MID = SIZE / 2;

/**
 * Open incidents grouped by category as a donut with the open total in the centre and a legend of
 * shares. Derived live from the incident list.
 */
export function CategoryDonut({ data, total }: CategoryDonutProps) {
  let offset = 0;

  return (
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
          {data.map((slice, i) => {
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
          <span className="text-caption font-medium text-muted">Open</span>
        </div>
      </div>

      <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
        {data.map((slice, i) => (
          <li key={slice.key} className="flex items-center gap-2 text-sm">
            <span
              className="size-2 shrink-0 rounded-full"
              style={{ backgroundColor: PALETTE[i % PALETTE.length] }}
              aria-hidden
            />
            <span className="min-w-0 flex-1 truncate text-ink-soft">{slice.label}</span>
            <span className="ml-auto tnum font-semibold text-ink">{slice.pct}%</span>
            <span className="w-6 text-right tnum text-muted">{slice.count}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
