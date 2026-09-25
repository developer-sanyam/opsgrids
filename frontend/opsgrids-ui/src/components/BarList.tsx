import { defaultFormat, percentages } from "../lib/chart";
import { cn } from "../lib/cn";
import { SERIES_TONE } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { ChartDataTable } from "./ChartParts";

export interface BarListItem {
  key: string;
  label: string;
  value: number;
}

interface BarListProps {
  /** What is broken down: "Committed spend by category". Its table's caption. */
  label: string;
  /** Largest first reads best; the list is drawn in the order given. */
  items: BarListItem[];
  formatValue?: ((value: number) => string) | undefined;
  /** Show each row's share of the total beside its value. Default on. */
  showShare?: boolean | undefined;
  /** The bars' colour. Default the accent: the labels say which row is which, not the hue. */
  tone?: Tone | undefined;
  className?: string | undefined;
}

/**
 * A ranked breakdown — name, value, share, and a bar for the proportion — the most common chart
 * in the suite (eleven hand-rolled copies: pipeline stages, ageing buckets, spend by category).
 *
 * Every figure is on screen as text, so the bars only restate them. The visible list is hidden
 * from assistive technology in favour of the same data as a table, the way every chart here is
 * read. Bars are measured against the largest value, so the top row always fills its track.
 */
export function BarList({
  label,
  items,
  formatValue = defaultFormat,
  showShare = true,
  tone = "accent",
  className,
}: BarListProps) {
  const largest = Math.max(0, ...items.map((item) => item.value));
  const shares = percentages(items.map((item) => item.value));

  return (
    <figure className={cn("m-0", className)}>
      <ul aria-hidden className="flex flex-col gap-3.5">
        {items.map((item, index) => (
          <li key={item.key} className="flex flex-col gap-1.5">
            <div className="flex items-baseline gap-3 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium text-ink">{item.label}</span>
              <span className="tnum font-semibold text-ink">{formatValue(item.value)}</span>
              {showShare ? <span className="tnum w-10 text-right text-muted">{shares[index]}%</span> : null}
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-ground">
              <div
                className={cn("h-full rounded-full", SERIES_TONE[tone].swatch)}
                style={{ width: `${largest === 0 ? 0 : Math.max((item.value / largest) * 100, item.value > 0 ? 2 : 0)}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
      <ChartDataTable
        caption={label}
        headers={showShare ? ["Item", "Value", "Share"] : ["Item", "Value"]}
        rows={items.map((item, index) => ({
          key: item.key,
          cells: [item.label, formatValue(item.value), ...(showShare ? [`${shares[index]}%`] : [])],
        }))}
      />
    </figure>
  );
}
