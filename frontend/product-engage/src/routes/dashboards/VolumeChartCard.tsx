import { BarChartIcon, ChartCard, EmptyState } from "@opsgrids/ui";
import type { Tone } from "@opsgrids/ui";

import { formatDay } from "./dates";
import { TrendChart } from "./TrendChart";

export interface VolumeSeries {
  key: string;
  label: string;
  tone: Tone;
  values: number[];
}

/**
 * A count-per-day chart in its card, with the card's own loading and empty states. The chart is
 * `LineChart` (or bars for a single day), which draws the plot and also ships every value as an
 * off-screen table (day by series) — that table is this chart's text alternative, and it is
 * present whenever the plot is.
 * A range with nothing in it is an empty state, not a flat line along zero.
 */
export function VolumeChartCard({
  title,
  description,
  chartLabel,
  dates,
  series,
  loading = false,
  emptyTitle,
  emptyDescription,
  className,
}: {
  title: string;
  description?: string;
  /** The plot's accessible name and its table's caption. */
  chartLabel: string;
  /** `YYYY-MM-DD`, one per point, in order. */
  dates: string[];
  series: VolumeSeries[];
  loading?: boolean;
  emptyTitle: string;
  emptyDescription: string;
  className?: string;
}) {
  const nothing = dates.length === 0 || series.every((one) => one.values.every((value) => value === 0));
  return (
    <ChartCard
      title={title}
      {...(description !== undefined ? { description } : {})}
      loading={loading}
      {...(className !== undefined ? { className } : {})}
      empty={
        nothing ? (
          <EmptyState
            icon={<BarChartIcon className="size-6" />}
            title={emptyTitle}
            description={emptyDescription}
          />
        ) : undefined
      }
    >
      <TrendChart
        label={chartLabel}
        categories={dates.map(formatDay)}
        series={series.map((one) => ({
          key: one.key,
          label: one.label,
          values: one.values,
          tone: one.tone,
        }))}
        height="lg"
      />
    </ChartCard>
  );
}
