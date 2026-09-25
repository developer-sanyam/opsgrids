import type { ReactNode } from "react";

import { ArrowDown, ArrowUp } from "../icons";
import { cn } from "../lib/cn";
import { ICON_TILE } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { Card } from "./Card";
import { SampleChip } from "./SampleChip";

export interface MetricTrend {
  /** The change, as a percentage: `12` is up 12%, `-33` is down 33%. */
  value: number;
  /**
   * Which way is good for this metric. Default `up`; `down` for suspended accounts, open
   * incidents, overdue balance. Sets the colour; the arrow always shows the literal direction.
   */
  goodWhen?: "up" | "down" | undefined;
  /** What it is compared with: "vs last month". */
  period?: string | undefined;
}

interface MetricCardProps {
  label: string;
  /** Pre-formatted: "128", "₹12,48,320", "94.2%". */
  value: string;
  /** A small icon in a pastel tile. */
  icon?: ReactNode | undefined;
  /** The tile's family. Default `accent`. */
  tone?: Tone | undefined;
  /**
   * The change against a comparison period. **Omit it when there is none** — no trend endpoint,
   * a first-ever reading — and the card shows no trend at all, rather than an invented one or a
   * dash that looks like "no change".
   */
  trend?: MetricTrend | undefined;
  /**
   * `default`: icon beside the figures, for a desktop KPI row. `compact`: stacked and smaller,
   * for the two-up row on a phone (mock-1, mock-5).
   */
  size?: "default" | "compact" | undefined;
  /** The figure is representative, not live: the amber chip. */
  sample?: boolean | { label?: string | undefined; title?: string | undefined } | undefined;
  className?: string | undefined;
}

const PERCENT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

/**
 * The KPI card as the brief draws it (§6): a small icon, a label, a large value, and — only when
 * there is one — a small trend with its comparison period. Nothing else; a card that needs more
 * is a `ChartCard`.
 *
 * The trend says its direction three ways that do not depend on colour: the arrow, and the words
 * "Up" or "Down" read to a screen reader ahead of the figure. The colour (green when the move is
 * good for this metric, red when it is not) reaches the arrow; the figure itself stays on ink, so
 * it is legible whichever way it went.
 */
export function MetricCard({
  label,
  value,
  icon,
  tone = "accent",
  trend,
  size = "default",
  sample = false,
  className,
}: MetricCardProps) {
  const compact = size === "compact";
  const rising = trend !== undefined && trend.value >= 0;
  const good = trend !== undefined && rising === ((trend.goodWhen ?? "up") === "up");
  const Arrow = rising ? ArrowUp : ArrowDown;

  return (
    <Card className={cn("flex min-w-0", compact ? "flex-col gap-3 p-4" : "items-start gap-4 p-4 sm:p-6", className)}>
      {icon ? (
        <span
          aria-hidden
          className={cn("grid shrink-0 place-items-center rounded-tile", compact ? "size-9" : "size-11", ICON_TILE[tone])}
        >
          {icon}
        </span>
      ) : null}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className={cn("min-w-0 font-medium text-muted", compact ? "text-xs" : "text-sm")}>{label}</p>
          {sample ? <SampleChip {...(sample === true ? {} : sample)} /> : null}
        </div>
        <p className={cn("tnum font-bold leading-tight text-ink", compact ? "text-xl" : "text-2xl")}>{value}</p>
        {trend !== undefined ? (
          <p className={cn("flex flex-wrap items-center gap-x-1.5", compact ? "text-xs" : "text-sm")}>
            <span className="tnum inline-flex items-center gap-0.5 font-semibold text-ink">
              <Arrow className={cn("size-3.5", good ? "text-positive" : "text-negative")} strokeWidth={2.4} aria-hidden />
              <span className="sr-only">{rising ? "Up" : "Down"} </span>
              {PERCENT.format(Math.abs(trend.value))}%
            </span>
            {trend.period ? <span className="text-muted">{trend.period}</span> : null}
          </p>
        ) : null}
      </div>
    </Card>
  );
}
