import { cn } from "../lib/cn";
import { formatCount } from "../lib/importExportCopy";

export interface ImportExportStat {
  label: string;
  value: number;
}

interface ImportExportStatsProps {
  stats: readonly ImportExportStat[];
  /** The grid's columns at each width, e.g. `"grid-cols-2 sm:grid-cols-4"`. */
  columns: string;
}

/**
 * Counts as a term and its figure. The word is what carries the meaning ("Need fixing", "Failed");
 * nothing here is coloured, so no count depends on a hue to be understood.
 */
export function ImportExportStats({ stats, columns }: ImportExportStatsProps) {
  return (
    <dl className={cn("grid gap-3", columns)}>
      {stats.map((stat) => (
        <div key={stat.label} className="flex flex-col gap-1 rounded-tile bg-sunken px-3 py-3">
          <dt className="text-caption text-muted">{stat.label}</dt>
          <dd className="text-kpi-sm tnum text-ink">{formatCount(stat.value)}</dd>
        </div>
      ))}
    </dl>
  );
}
