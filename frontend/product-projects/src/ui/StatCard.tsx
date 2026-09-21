import type { ReactNode } from "react";

import { Card, cn } from "@opsgrids/ui";
import type { KpiTone } from "@opsgrids/ui";

const TONE: Record<KpiTone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  positive: "bg-present-soft text-present",
  info: "bg-info-soft text-info",
  cyan: "bg-cyan-soft text-cyan",
  warning: "bg-late-soft text-late",
  danger: "bg-danger-soft text-danger",
};

/**
 * A live headline figure with nowhere to source a trend: `@opsgrids/ui`'s `KpiCard` requires a
 * `deltaPct` and a `series`, which is right for a metric with a history endpoint behind it and
 * wrong for one that doesn't exist yet (`services/projects` has no such endpoint for active
 * projects, on-time rate or milestones due). Rather than inventing a delta to satisfy the prop or
 * forking `KpiCard` into this app, this is the pastel-icon-and-figure half of that card with no
 * trend chrome at all — an honest "no number to show" instead of a fabricated one.
 */
export function StatCard({
  label,
  value,
  icon,
  tone,
}: {
  label: string;
  value: string;
  icon: ReactNode;
  tone: KpiTone;
}) {
  return (
    <Card className="p-5 flex flex-col gap-4">
      <span className={cn("grid size-10 place-items-center rounded-tile", TONE[tone])}>{icon}</span>
      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-muted">{label}</span>
        <span className="display text-[2.1rem] leading-none font-extrabold tnum">{value}</span>
      </div>
    </Card>
  );
}
