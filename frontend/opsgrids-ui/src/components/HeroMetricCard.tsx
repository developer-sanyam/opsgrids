import type { ReactNode } from "react";

import { ArrowDown, ArrowUp } from "../icons";
import { cn } from "../lib/cn";
import { SERIES_TONE } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { SampleChip } from "./SampleChip";

interface HeroMetricCardProps {
  label: string;
  /** Pre-formatted: "₹12,48,320". */
  value: string;
  icon: ReactNode;
  tone?: Tone | undefined;
  /** Omit — with `bars` — for a figure with no history to compare against. */
  deltaPct?: number | undefined;
  /** Which direction is good for this metric. Default `up`. */
  goodWhen?: "up" | "down" | undefined;
  /** What the delta is measured against ("from last month"), or, with no delta, the honest
   *  substitute ("This month") — never both. */
  caption?: string | undefined;
  /**
   * The real trailing months behind the figure, oldest first, last one current — never a
   * decorative or invented series. Omit where there is no history yet.
   */
  bars?: number[] | undefined;
  sample?: boolean | { label?: string | undefined; title?: string | undefined } | undefined;
  className?: string | undefined;
}

/** A tiny ascending bar chart, one bar per trailing month — the hero's real history, not a
 *  sparkline: the mockup draws bars here, and every value is still in the accessible data
 *  the card's own text already carries, so the chart itself is decorative. */
function MiniBars({ values, tone }: { values: number[]; tone: Tone }) {
  if (values.length === 0) return null;
  const max = Math.max(0, ...values) || 1;
  const swatch = SERIES_TONE[tone].swatch;
  return (
    <div aria-hidden className="flex h-10 shrink-0 items-end gap-1">
      {values.map((value, index) => (
        <div
          key={index}
          className={cn("w-1.5 rounded-chip", swatch)}
          style={{
            height: `${Math.max((value / max) * 100, 10)}%`,
            opacity: 0.3 + (index / Math.max(values.length - 1, 1)) * 0.7,
          }}
        />
      ))}
    </div>
  );
}

const PERCENT = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 });

/**
 * The phone home's hero card (mock-2, mock-5): a solid tone-filled round icon, the figure and its
 * trend stacked beside it, and — only with real history behind it — a mini bar chart pinned to
 * the card's own soft tint. There is exactly one caller today (Owner Home's Total Revenue on a
 * phone); it lives here rather than inline because a hero metric is exactly the kind of one-off
 * markup CLAUDE.md asks to not fork per app.
 */
export function HeroMetricCard({
  label,
  value,
  icon,
  tone = "accent",
  deltaPct,
  goodWhen = "up",
  caption,
  bars,
  sample = false,
  className,
}: HeroMetricCardProps) {
  const rising = deltaPct !== undefined && deltaPct >= 0;
  const good = deltaPct !== undefined && rising === (goodWhen === "up");
  const Arrow = rising ? ArrowUp : ArrowDown;

  return (
    <div className={cn("flex items-center gap-4 overflow-hidden rounded-card bg-accent-soft p-4 shadow-card sm:p-6", className)}>
      <span aria-hidden className="grid size-12 shrink-0 place-items-center rounded-full bg-accent text-on-accent">
        {icon}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2">
          <p className="min-w-0 truncate text-sm font-medium text-ink-soft">{label}</p>
          {sample ? <SampleChip {...(sample === true ? {} : sample)} /> : null}
        </div>
        <p data-kpi-value className="tnum min-w-0 truncate text-2xl font-bold leading-tight text-ink">
          {value}
        </p>
        {deltaPct !== undefined || caption ? (
          <p className="flex flex-wrap items-center gap-x-1.5 text-sm">
            {deltaPct !== undefined ? (
              <span className="tnum inline-flex items-center gap-0.5 font-semibold text-ink">
                <Arrow className={cn("size-3.5", good ? "text-positive" : "text-negative")} strokeWidth={2.4} aria-hidden />
                <span className="sr-only">{rising ? "Up" : "Down"} </span>
                {PERCENT.format(Math.abs(deltaPct))}%
              </span>
            ) : null}
            {caption ? <span className="text-muted">{caption}</span> : null}
          </p>
        ) : null}
      </div>
      {bars && bars.length > 0 ? <MiniBars values={bars} tone={tone} /> : null}
    </div>
  );
}
