import { defaultFormat, labelStride, scaleFor, yOf } from "../lib/chart";
import { cn } from "../lib/cn";
import { SERIES_TONE, seriesTone } from "../lib/tones";
import { useChartCursor } from "../lib/useChartCursor";
import { CategoryAxis, ChartDataTable, ChartLegend, ChartTooltip, ValueAxis } from "./ChartParts";
import type { ChartSeries } from "./LineChart";

const HEIGHT = { sm: "h-32", md: "h-48", lg: "h-64" };

interface BarChartProps {
  /** What the chart shows: "Tickets opened and solved, last 7 days". Its name and its table's caption. */
  label: string;
  /** One group of bars per category: "Mon", "Tue"… */
  categories: string[];
  /** One bar per series in each group — opened beside solved. */
  series: ChartSeries[];
  formatValue?: ((value: number) => string) | undefined;
  height?: keyof typeof HEIGHT | undefined;
  className?: string | undefined;
}

/**
 * Vertical bars for counts per period, grouped when there is more than one series — from the
 * support and operations volume charts. HTML bars, not SVG, so they stay crisp at any width.
 *
 * Accessible the way `LineChart` is: one focusable, named plot whose tooltip steps with Left and
 * Right, and an off-screen table of every value. For a share of a whole, use `BarList` or
 * `DonutChart`; for a trend, `LineChart`.
 */
export function BarChart({
  label,
  categories,
  series,
  formatValue = defaultFormat,
  height = "md",
  className,
}: BarChartProps) {
  const count = categories.length;
  const scale = scaleFor(series.flatMap((one) => one.values));
  const zero = yOf(0, scale);
  const { active, handlers } = useChartCursor(count, "bands");
  const tones = series.map((one, index) => seriesTone(index, one.tone));
  const centre = (index: number) => (count === 0 ? 50 : ((index + 0.5) / count) * 100);

  return (
    <figure className={cn("m-0 flex flex-col gap-3", className)}>
      {series.length > 1 ? (
        <ChartLegend items={series.map((one, index) => ({ key: one.key, label: one.label, tone: tones[index] ?? "accent" }))} />
      ) : null}
      <div className="ml-12">
        <div
          role="img"
          aria-label={label}
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex -- focusable so a keyboard can step the tooltip through the bars; the keys change nothing but what is shown.
          tabIndex={0}
          {...handlers}
          className={cn("relative rounded-tile focus-visible:outline-offset-4", HEIGHT[height])}
        >
          <ValueAxis scale={scale} format={formatValue} />
          <div aria-hidden className="absolute inset-0 flex">
            {categories.map((category, index) => (
              <div
                key={`${category}-${index}`}
                className={cn(
                  "relative flex h-full min-w-0 flex-1 justify-center gap-1 rounded-tile px-1 transition-colors",
                  active === index && "bg-ground/70",
                )}
              >
                {series.map((one, position) => {
                  const value = one.values[index] ?? 0;
                  const top = Math.min(yOf(value, scale), zero);
                  const size = Math.abs(yOf(value, scale) - zero);
                  return (
                    <div key={one.key} className="relative h-full w-full max-w-4">
                      <div
                        className={cn(
                          "absolute inset-x-0",
                          value >= 0 ? "rounded-t-full" : "rounded-b-full",
                          SERIES_TONE[tones[position] ?? "accent"].swatch,
                        )}
                        style={{ top: `${top}%`, height: `${size}%` }}
                      />
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
          {active !== null ? (
            <ChartTooltip left={centre(active)}>
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
        <CategoryAxis categories={categories} stride={labelStride(count, 8)} position={centre} />
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
