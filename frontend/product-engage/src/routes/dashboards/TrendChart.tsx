import { BarChart, LineChart } from "@opsgrids/ui";
import type { ChartSeries } from "@opsgrids/ui";

import { formatNumber } from "@/lib/format";

/**
 * A count-per-day chart that stays visible for a one-day range. A line through one point has no
 * length and draws nothing, so a single day is drawn as bars — the same data, the same off-screen
 * table, and a shape that exists.
 */
export function TrendChart({
  label,
  categories,
  series,
  height,
}: {
  /** The plot's accessible name and its table's caption. */
  label: string;
  categories: string[];
  series: ChartSeries[];
  height: "md" | "lg";
}) {
  if (categories.length < 2) {
    return (
      <BarChart label={label} categories={categories} series={series} formatValue={formatNumber} height={height} />
    );
  }
  return (
    <LineChart
      label={label}
      categories={categories}
      series={series}
      formatValue={formatNumber}
      integer
      height={height}
    />
  );
}
