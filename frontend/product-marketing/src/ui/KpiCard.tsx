import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { Sparkline } from "./Sparkline";
import { MoreHorizontalIcon } from "./icons";

export type KpiTone = "accent" | "positive" | "info" | "cyan";

const TONE: Record<KpiTone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  positive: "bg-present-soft text-present",
  info: "bg-info-soft text-info",
  cyan: "bg-cyan-soft text-cyan",
};

interface KpiCardProps {
  label: string;
  /** Pre-formatted headline (e.g. "₹8.5Cr", "4.2×"). */
  value: string;
  deltaPct: number;
  /** Which direction is the good one — "down" for a cost metric like cost-per-lead. */
  goodWhen?: "up" | "down";
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  /** A one-line note under the value, e.g. "vs last quarter". */
  caption?: string;
}

/**
 * A KPI card: a pastel icon square, an inline trend sparkline and a "…" menu across the top, then
 * a label, a big tabular value and a coloured delta pill whose colour reflects good vs bad, not
 * merely up vs down.
 */
export function KpiCard({
  label,
  value,
  deltaPct,
  goodWhen = "up",
  series,
  icon,
  tone,
  caption,
}: KpiCardProps) {
  const rising = deltaPct >= 0;
  const good = rising === (goodWhen === "up");
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <span className={cn("grid size-10 place-items-center rounded-tile", TONE[tone])}>
          {icon}
        </span>
        <div className="flex items-center gap-2">
          <Sparkline data={series} trend={good ? "up" : "down"} />
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
        <span className="text-sm font-medium text-muted">{label}</span>
        <div className="flex flex-wrap items-center gap-2.5">
          <span className="display text-[2.1rem] leading-none font-extrabold tnum">{value}</span>
          <DeltaPill value={deltaPct} goodWhen={goodWhen} />
        </div>
        {caption ? <span className="text-xs text-faint">{caption}</span> : null}
      </div>
    </Card>
  );
}
