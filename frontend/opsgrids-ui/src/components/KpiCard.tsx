import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { MoreHorizontalIcon } from "../icons";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { SampleChip } from "./SampleChip";
import { Sparkline } from "./Sparkline";

/** The pastel square behind the icon. Semantic families only — never a decorative sixth colour. */
export type KpiTone = "accent" | "positive" | "info" | "cyan" | "warning" | "danger";

const TONE: Record<KpiTone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  positive: "bg-present-soft text-present",
  info: "bg-info-soft text-info",
  cyan: "bg-cyan-soft text-cyan",
  warning: "bg-late-soft text-late",
  danger: "bg-danger-soft text-danger",
};

interface KpiCardProps {
  label: string;
  /** Pre-formatted headline (e.g. "24", "94.2%", "₹70.38L"). */
  value: string;
  deltaPct: number;
  series: number[];
  icon: ReactNode;
  tone: KpiTone;
  /**
   * Which direction is good for this metric. Defaults to `"up"`; pass `"down"` for open
   * incidents, breaches, cost per lead or overdue balance. Drives both the delta pill's colour
   * and the sparkline's, while the arrow keeps showing the literal direction.
   */
  goodWhen?: "up" | "down";
  /** A one-line qualifier — "Net of credit notes", "vs last quarter". */
  caption?: string;
  /**
   * Where the caption sits. `"value"` (default) puts it below the figure; `"label"` stacks it
   * directly under a heavier label, for a metric whose name needs the qualifier to be read.
   */
  captionPlacement?: "label" | "value";
  /**
   * Marks the trend as representative rather than live — renders the amber chip. `true` takes
   * the design system's wording; pass an object to say which service is missing in this
   * product's words, and to switch the chip to "Sample trend" where the headline is live and
   * only the delta and sparkline are stand-ins.
   */
  sample?: boolean | { label?: string; title?: string };
  /** The headline face: tabular sans (default), or the monospaced `.money` face for amounts. */
  figure?: "tabular" | "money";
}

/**
 * A dashboard KPI: a pastel icon square, an inline trend sparkline and a "…" menu across the top,
 * then a label, a big tabular value and a coloured delta pill whose colour tracks whether the
 * change is *good* for this metric — not merely which way it points.
 */
export function KpiCard({
  label,
  value,
  deltaPct,
  series,
  icon,
  tone,
  goodWhen = "up",
  caption,
  captionPlacement = "value",
  sample = false,
  figure = "tabular",
}: KpiCardProps) {
  const good = deltaPct >= 0 === (goodWhen === "up");
  const chip = sample ? <SampleChip {...(sample === true ? {} : sample)} /> : null;

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
        {captionPlacement === "label" ? (
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-ink-soft">{label}</span>
              {chip}
            </div>
            {caption ? <span className="text-xs text-faint">{caption}</span> : null}
          </div>
        ) : (
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-muted">{label}</span>
            {chip}
          </div>
        )}

        <div className="flex flex-wrap items-center gap-2.5">
          <span
            className={
              figure === "money"
                ? "money display text-[1.85rem] leading-none font-extrabold"
                : "display text-[2.1rem] leading-none font-extrabold tnum"
            }
          >
            {value}
          </span>
          <DeltaPill value={deltaPct} goodWhen={goodWhen} />
        </div>

        {captionPlacement === "value" && caption ? (
          <span className="text-xs text-faint">{caption}</span>
        ) : null}
      </div>
    </Card>
  );
}
