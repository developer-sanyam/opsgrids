import type { HealthBucket } from "@/api/types";
import { PROJECT_STATUS_META } from "./statusMeta";

const R = 46;
const C = 2 * Math.PI * R;
const SIZE = 116;
const MID = SIZE / 2;

interface ProjectHealthProps {
  buckets: HealthBucket[];
  activeTotal: number;
  deliveredCount: number;
}

/**
 * The project-health breakdown: a donut over the active portfolio (on track / at risk / blocked)
 * with the active count in the centre, a legend, and a footer noting delivered work. Colours are
 * the shared semantic families, pulled from the same map the status pills use.
 */
export function ProjectHealth({ buckets, activeTotal, deliveredCount }: ProjectHealthProps) {
  let offset = 0;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-5">
        <div className="relative shrink-0">
          <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden>
            <circle cx={MID} cy={MID} r={R} fill="none" stroke="var(--color-ground)" strokeWidth={12} />
            {buckets.map((bucket) => {
              if (bucket.count === 0) return null;
              const len = (bucket.pct / 100) * C;
              const dash = `${len} ${C - len}`;
              const el = (
                <circle
                  key={bucket.status}
                  cx={MID}
                  cy={MID}
                  r={R}
                  fill="none"
                  stroke={PROJECT_STATUS_META[bucket.status].color}
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
            <span className="display text-2xl font-extrabold tnum leading-none">{activeTotal}</span>
            <span className="text-[0.7rem] font-medium text-muted">Active</span>
          </div>
        </div>

        <ul className="flex min-w-0 flex-1 flex-col gap-2.5">
          {buckets.map((bucket) => (
            <li key={bucket.status} className="flex items-center gap-2 text-sm">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ backgroundColor: PROJECT_STATUS_META[bucket.status].color }}
                aria-hidden
              />
              <span className="min-w-0 flex-1 truncate text-ink-soft">{bucket.label}</span>
              <span className="ml-auto tnum font-semibold text-ink">{bucket.count}</span>
              <span className="w-10 text-right tnum text-muted">{bucket.pct}%</span>
            </li>
          ))}
        </ul>
      </div>

      <p className="border-t border-line pt-3 text-sm text-muted">
        <span className="tnum font-semibold text-ink">{deliveredCount}</span> delivered this
        quarter · <span className="tnum font-semibold text-ink">{activeTotal}</span> in flight
      </p>
    </div>
  );
}
