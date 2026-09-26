import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import { ICON_TILE } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { Card } from "./Card";
import { DeltaPill } from "./DeltaPill";
import { SampleChip } from "./SampleChip";
import { Sparkline } from "./Sparkline";

/** The pastel square behind the icon. Semantic families only — never a decorative sixth colour. */
export type KpiTone = Tone;

interface KpiCardProps {
  label: string;
  /** Pre-formatted headline (e.g. "24", "94.2%", "₹70.38L"). */
  value: string;
  /**
   * The change, as a percentage. Omit it — with `series` — for a figure that has no history to
   * compare against: no trend endpoint, or a metric taken today for the first time. An invented
   * delta is worse than no delta.
   */
  deltaPct?: number | undefined;
  /** The trend behind the figure. Omit it where there is none; the card renders without it. */
  series?: number[] | undefined;
  icon: ReactNode;
  tone: KpiTone;
  /**
   * Which direction is good for this metric. Defaults to `"up"`; pass `"down"` for open
   * incidents, breaches, cost per lead or overdue balance. Drives both the delta pill's colour
   * and the sparkline's, while the arrow keeps showing the literal direction.
   */
  goodWhen?: "up" | "down" | undefined;
  /** A one-line qualifier — "Net of credit notes", "vs last quarter". */
  caption?: string | undefined;
  /**
   * Where the caption sits. `"value"` (default) puts it below the figure; `"label"` stacks it
   * directly under a heavier label, for a metric whose name needs the qualifier to be read.
   */
  captionPlacement?: "label" | "value" | undefined;
  /**
   * Marks the trend as representative rather than live — renders the amber chip. `true` takes
   * the design system's wording; pass an object to say which service is missing in this
   * product's words, and to switch the chip to "Sample trend" where the headline is live and
   * only the delta and sparkline are stand-ins.
   */
  sample?: boolean | { label?: string | undefined; title?: string | undefined } | undefined;
  /** The headline face: tabular sans (default), or the monospaced `.money` face for amounts. */
  figure?: "tabular" | "money" | undefined;
}

/**
 * A dashboard KPI: a pastel icon square and an inline trend sparkline across the top,
 * then a label, a big tabular value and a coloured delta pill whose colour tracks whether the
 * change is *good* for this metric — not merely which way it points.
 *
 * **`deltaPct` and `series` are both optional.** A live figure with no trend behind it renders as
 * icon, label, value and caption, and nothing else — which is the honest thing to show, and the
 * reason no app needs a second stat card of its own.
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
  // With no delta to judge, the sparkline is drawn in the neutral-good tone rather than red:
  // an absent comparison is not a bad one.
  const good = deltaPct === undefined || deltaPct >= 0 === (goodWhen === "up");
  const chip = sample ? <SampleChip {...(sample === true ? {} : sample)} /> : null;

  return (
    <Card className="flex flex-col gap-4 p-4 sm:p-6">
      <div className="flex items-start justify-between">
        <span className={cn("grid size-10 place-items-center rounded-tile", ICON_TILE[tone])}>
          {icon}
        </span>
        {series !== undefined ? <Sparkline data={series} tone={good ? "good" : "bad"} /> : null}
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

        <div className="flex flex-wrap items-center gap-2">
          <span data-kpi-value className={cn("text-kpi", figure === "money" ? "money" : "tnum")}>
            {value}
          </span>
          {deltaPct !== undefined ? <DeltaPill value={deltaPct} goodWhen={goodWhen} /> : null}
        </div>

        {captionPlacement === "value" && caption ? (
          <span className="text-xs text-faint">{caption}</span>
        ) : null}
      </div>
    </Card>
  );
}
