import type { ReactNode } from "react";

import { cn } from "@/lib/cn";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { Sparkline } from "./Sparkline";
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
  /** Pre-formatted headline (e.g. "₹70.38L"). Rendered with the mono figure face. */
  value: string;
  deltaPct: number;
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  /** A one-line qualifier under the label (e.g. "Net of credit notes"). */
  caption?: string;
  /** When a rise is bad news (overdue climbing), flip the delta tone to follow meaning. */
  invertDelta?: boolean;
}

/**
 * A dashboard KPI: a pastel icon square, an inline trend sparkline and a "…" menu across the top,
 * then a label (+ caption), a big tabular money value and a coloured delta pill.
 */
export function KpiCard({
  label,
  value,
  deltaPct,
  series,
  icon,
  tone,
  caption,
  invertDelta = false,
}: KpiCardProps) {
  return (
    <Card className="p-5 flex flex-col gap-4">
      <div className="flex items-start justify-between">
        <span className={cn("grid size-10 place-items-center rounded-tile", TONE[tone])}>
          {icon}
        </span>
        <div className="flex items-center gap-2">
          <Sparkline data={series} trend={deltaPct >= 0 ? "up" : "down"} />
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
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-semibold text-ink-soft">{label}</span>
          {caption ? <span className="text-xs text-faint">{caption}</span> : null}
        </div>
        <div className="flex items-center gap-2.5">
          <span className="money display text-[1.85rem] leading-none font-extrabold">
            {value}
          </span>
          <DeltaPill value={deltaPct} invert={invertDelta} />
        </div>
      </div>
    </Card>
  );
}
