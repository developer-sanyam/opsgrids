import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { Sparkline } from "./Sparkline";

export type KpiTone = "accent" | "active" | "trial" | "comp" | "suspended" | "pastdue";

const TONE: Record<KpiTone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  active: "bg-present-soft text-present",
  trial: "bg-info-soft text-info",
  comp: "bg-cyan-soft text-cyan",
  suspended: "bg-danger-soft text-danger",
  pastdue: "bg-late-soft text-late",
};

interface KpiCardProps {
  label: string;
  /** Pre-formatted headline (e.g. "₹17.6L", "9", "66.7%"). */
  value: string;
  /** `null` when the metrics endpoint carries no period-over-period comparison for this figure —
   *  the pill is omitted rather than showing a fabricated "+0.0%". */
  deltaPct: number | null;
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  /** A short qualifier under the value (e.g. "vs last month"). */
  caption?: string;
  /** Whether a rise is the good outcome; false for churn (a fall is good). */
  positiveIsGood?: boolean;
  /** Hide the sparkline for count metrics where a trend line adds nothing. */
  hideSparkline?: boolean;
}

/**
 * A dashboard KPI: a tinted icon square and an inline trend sparkline across the top, then a label,
 * a big tabular value and — when there is one — a coloured delta pill.
 */
export function KpiCard({
  label,
  value,
  deltaPct,
  series,
  icon,
  tone,
  caption,
  positiveIsGood = true,
  hideSparkline = false,
}: KpiCardProps) {
  return (
    <Card className="flex flex-col gap-4 p-5">
      <div className="flex items-start justify-between">
        <span className={cn("grid size-10 place-items-center rounded-tile", TONE[tone])}>
          {icon}
        </span>
        {hideSparkline || deltaPct === null ? null : (
          <Sparkline data={series} trend={deltaPct >= 0 ? "up" : "down"} />
        )}
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-sm font-medium text-muted">{label}</span>
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="display text-[2rem] leading-none font-extrabold tnum">{value}</span>
          {deltaPct === null ? null : <DeltaPill value={deltaPct} positiveIsGood={positiveIsGood} />}
        </div>
        {caption ? <span className="text-xs text-faint">{caption}</span> : null}
      </div>
    </Card>
  );
}
