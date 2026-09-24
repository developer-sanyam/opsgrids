import type { CSSProperties } from "react";

import type { BreakdownSegment } from "@/api/types";

const SEGMENT_FILL: Record<BreakdownSegment["tone"], string> = {
  active: "bg-present",
  trial: "bg-info",
  pastdue: "bg-late",
  suspended: "bg-danger",
  cancelled: "bg-muted",
  comp: "bg-cyan",
  accent: "bg-accent",
};

/**
 * A labelled, stacked proportion bar with a legend beneath. Used for the trial funnel and the
 * lifecycle split. Each legend row names its segment and count, so the meaning is never carried by
 * colour alone; the bar itself is one image with the whole breakdown as its label.
 *
 * A segment's share reaches the stylesheet as a custom property (`--share`) that a class turns
 * into a width — the one inline style the token rules allow, because the class still decides what
 * the number means.
 */
export function BreakdownBar({ segments, unit }: { segments: BreakdownSegment[]; unit: string }) {
  const total = segments.reduce((sum, s) => sum + s.count, 0) || 1;

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex h-3 w-full overflow-hidden rounded-full bg-ground"
        role="img"
        aria-label={segments.map((s) => `${s.label}: ${s.count} ${unit}`).join(", ")}
      >
        {segments.map((s) =>
          s.count > 0 ? (
            <span
              key={s.key}
              className={`h-full w-(--share) ${SEGMENT_FILL[s.tone]}`}
              style={{ "--share": `${(s.count / total) * 100}%` } as CSSProperties}
            />
          ) : null,
        )}
      </div>

      <ul className="flex flex-col gap-2">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-2 text-sm">
            <span className={`size-2 shrink-0 rounded-full ${SEGMENT_FILL[s.tone]}`} aria-hidden />
            <span className="text-ink-soft">{s.label}</span>
            <span className="ml-auto tnum font-bold text-ink">{s.count}</span>
            <span className="w-12 text-right tnum text-muted">{Math.round((s.count / total) * 100)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
