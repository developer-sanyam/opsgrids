import type { BreakdownSegment } from "@/api/types";

const SEGMENT_COLOR: Record<BreakdownSegment["tone"], string> = {
  active: "var(--color-present)",
  trial: "var(--color-info)",
  pastdue: "var(--color-late)",
  suspended: "var(--color-danger)",
  cancelled: "var(--color-neutral)",
  comp: "var(--color-cyan)",
  accent: "var(--color-accent)",
};

/**
 * A labelled, stacked proportion bar with a legend beneath. Used for the trial funnel and the
 * lifecycle/churn split. Each legend row names its segment and count, so the meaning is never
 * carried by colour alone.
 */
export function BreakdownBar({
  segments,
  unit,
}: {
  segments: BreakdownSegment[];
  unit: string;
}) {
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
              className="h-full"
              style={{ width: `${(s.count / total) * 100}%`, backgroundColor: SEGMENT_COLOR[s.tone] }}
            />
          ) : null,
        )}
      </div>

      <ul className="flex flex-col gap-2.5">
        {segments.map((s) => (
          <li key={s.key} className="flex items-center gap-2.5 text-sm">
            <span
              className="size-2.5 shrink-0 rounded-full"
              style={{ backgroundColor: SEGMENT_COLOR[s.tone] }}
              aria-hidden
            />
            <span className="text-ink-soft">{s.label}</span>
            <span className="ml-auto tnum font-bold text-ink">{s.count}</span>
            <span className="w-12 text-right tnum text-muted">
              {Math.round((s.count / total) * 100)}%
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
