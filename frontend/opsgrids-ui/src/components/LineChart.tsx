import { useId } from "react";

import { areaPath, defaultFormat, labelStride, linePath, scaleFor, smoothAreaPath, smoothLinePath, xOf, yOf } from "../lib/chart";
import { cn } from "../lib/cn";
import { SERIES_TONE, seriesTone } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { useChartCursor } from "../lib/useChartCursor";
import { CategoryAxis, ChartDataTable, ChartLegend, ChartTooltip, ValueAxis } from "./ChartParts";

export type ChartTone = Tone;

export interface ChartSeries {
  key: string;
  /** "Inflow", "New accounts". The legend's and the data table's column name. */
  label: string;
  /** One value per category, in order. */
  values: number[];
  /**
   * The series' colour, from the semantic families. Name it when colour carries meaning — outflow
   * in `danger` beside inflow in the accent; otherwise series take the accent, then info, cyan…
   */
  tone?: ChartTone | undefined;
}

const HEIGHT = { sm: "h-32", md: "h-48", lg: "h-64" };

interface LineChartProps {
  /** What the chart shows: "New accounts per week". Its accessible name and its table's caption. */
  label: string;
  /** The x axis, in order: "25 Aug", "1 Sep"… */
  categories: string[];
  series: ChartSeries[];
  /** Filled under the line, for a single quantity over time. Default on for one series, off for several. */
  area?: boolean | undefined;
  /** Axis, tooltip and table values. Money charts pass their formatter (`formatMoneyCompact`). */
  formatValue?: ((value: number) => string) | undefined;
  /** The plot's height. Default `md` (192px). */
  height?: keyof typeof HEIGHT | undefined;
  /**
   * The value axis ticks on whole numbers only (never 0.5) — for a series that counts things
   * (accounts, trials, seats), where a small range would otherwise land `niceStep` on a
   * fraction. Leaves a wider-ranged series, or one that is not a count, unaffected.
   */
  integer?: boolean | undefined;
  /**
   * Curves the join between two points instead of a straight segment — a soft trend line rather
   * than a jagged one, for a series of small counts where sharp spikes read as noise. Every real
   * point still sits exactly where its value says; nothing is invented between them, only the
   * line's approach to each point is rounded off. Default off: a straight line never misreads.
   */
  smooth?: boolean | undefined;
  className?: string | undefined;
}

/**
 * A line or area chart for values over time, from the nine hand-drawn trends across the apps —
 * with no charting library, which would ship once per app (audit §5).
 *
 * **Accessible as data, not as a picture.** The plot is one focusable, named image; its values
 * are an off-screen table (category × series) that a screen reader reads by row and column. For a
 * sighted keyboard user, the focused plot steps through the points with Left and Right and shows
 * each in the tooltip a pointer gets on hover.
 *
 * The value axis starts at zero and ends on a round number; category labels thin out rather than
 * overlap on a narrow card. Values are plotted exactly as given — `smooth` softens how the line
 * approaches each one, never where the point itself sits.
 */
export function LineChart({
  label,
  categories,
  series,
  area,
  formatValue = defaultFormat,
  height = "md",
  integer = false,
  smooth = false,
  className,
}: LineChartProps) {
  const gradient = useId();
  const count = categories.length;
  const scale = scaleFor(series.flatMap((one) => one.values), 3, integer);
  const filled = area ?? series.length === 1;
  const { active, handlers } = useChartCursor(count, "points");
  const tones = series.map((one, index) => seriesTone(index, one.tone));
  const drawLine = smooth ? smoothLinePath : linePath;
  const drawArea = smooth ? smoothAreaPath : areaPath;

  return (
    <figure className={cn("m-0 flex flex-col gap-3", className)}>
      {series.length > 1 ? (
        <ChartLegend items={series.map((one, index) => ({ key: one.key, label: one.label, tone: tones[index] ?? "accent" }))} />
      ) : null}
      <div className="ml-12">
        <div
          role="img"
          aria-label={label}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- focusable so a keyboard can step the tooltip through the points; the keys change nothing but what is shown.
          tabIndex={0}
          {...handlers}
          className={cn("relative rounded-tile focus-visible:outline-offset-4", HEIGHT[height])}
        >
          <ValueAxis scale={scale} format={formatValue} />
          <svg aria-hidden viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 size-full overflow-visible">
            <defs>
              {series.map((one, index) => (
                <linearGradient
                  key={one.key}
                  id={`${gradient}-${index}`}
                  x1="0"
                  y1="0"
                  x2="0"
                  y2="1"
                  className={SERIES_TONE[tones[index] ?? "accent"].current}
                >
                  <stop offset="0%" stopColor="currentColor" stopOpacity="0.24" />
                  <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
                </linearGradient>
              ))}
            </defs>
            {filled
              ? series.map((one, index) => (
                  <path key={one.key} d={drawArea(one.values, scale)} fill={`url(#${gradient}-${index})`} />
                ))
              : null}
            {series.map((one, index) => (
              <path
                key={one.key}
                d={drawLine(one.values, scale)}
                fill="none"
                strokeWidth={2.25}
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
                className={SERIES_TONE[tones[index] ?? "accent"].stroke}
              />
            ))}
            {active !== null ? (
              <line
                x1={xOf(active, count)}
                x2={xOf(active, count)}
                y1={0}
                y2={100}
                strokeWidth={1}
                vectorEffect="non-scaling-stroke"
                className="stroke-muted"
              />
            ) : null}
          </svg>
          {active !== null
            ? series.map((one, index) => {
                const value = one.values[active];
                if (value === undefined) return null;
                return (
                  <span
                    key={one.key}
                    aria-hidden
                    className={cn(
                      "pointer-events-none absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface",
                      SERIES_TONE[tones[index] ?? "accent"].swatch,
                    )}
                    style={{ left: `${xOf(active, count)}%`, top: `${yOf(value, scale)}%` }}
                  />
                );
              })
            : null}
          {active !== null ? (
            <ChartTooltip left={xOf(active, count)}>
              <span className="text-surface/70">{categories[active]}</span>
              {series.map((one) => (
                <span key={one.key} className="tnum">
                  {series.length > 1 ? `${one.label} ` : ""}
                  {formatValue(one.values[active] ?? 0)}
                </span>
              ))}
            </ChartTooltip>
          ) : null}
        </div>
        <CategoryAxis categories={categories} stride={labelStride(count)} position={(index) => xOf(index, count)} />
      </div>
      <ChartDataTable
        caption={label}
        headers={["Category", ...series.map((one) => one.label)]}
        rows={categories.map((category, index) => ({
          key: `${category}-${index}`,
          cells: [category, ...series.map((one) => formatValue(one.values[index] ?? 0))],
        }))}
      />
    </figure>
  );
}
