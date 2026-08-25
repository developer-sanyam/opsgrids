import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { Sparkline } from "./Sparkline";
import { SampleChip } from "./SampleChip";
import { MoreHorizontalIcon } from "./icons";

export type KpiTone = "accent" | "positive" | "info" | "cyan" | "danger";

const TONE: Record<KpiTone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  positive: "bg-present-soft text-present",
  info: "bg-info-soft text-info",
  cyan: "bg-cyan-soft text-cyan",
  danger: "bg-danger-soft text-danger",
};

interface KpiCardProps {
  label: string;
  /** Pre-formatted headline (e.g. "19", "94.2%"). */
  value: string;
  deltaPct: number;
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  /** Whether a rising value is good for this metric. Open incidents → false. */
  higherIsBetter?: boolean;
  /**
   * The delta and sparkline are representative while no reporting service serves trend history.
   * The headline `value` stays live, so the chip says "Sample trend", not "Sample data".
   */
  sample?: boolean;
}

/**
 * An Overview KPI: a pastel icon square, an inline trend sparkline and a "…" menu across the top,
 * then a label, a big tabular value and a coloured delta pill whose colour tracks whether the
 * change is *good* for this metric (not merely up/down).
 */
export function KpiCard({
  label,
  value,
  deltaPct,
  series,
  icon,
  tone,
  higherIsBetter = true,
  sample = false,
}: KpiCardProps) {
  const rising = deltaPct >= 0;
  const good = rising === higherIsBetter;
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <span className={cn("grid size-10 place-items-center rounded-tile", TONE[tone])}>
          {icon}
        </span>
        <div className="flex items-center gap-2">
          <Sparkline data={series} tone={good ? "good" : "bad"} />
          <button
            type="button"
            aria-label={`More options for ${label}`}
            className="grid size-7 place-items-center rounded-full text-faint hover:bg-ground hover:text-ink transition-colors"
          >
            <MoreHorizontalIcon className="size-4" />
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-muted">{label}</span>
          {sample ? <SampleChip label="Sample trend" /> : null}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="display text-[2.1rem] leading-none font-extrabold tnum">
            {value}
          </span>
          <DeltaPill value={deltaPct} higherIsBetter={higherIsBetter} />
        </div>
      </div>
    </Card>
  );
}
