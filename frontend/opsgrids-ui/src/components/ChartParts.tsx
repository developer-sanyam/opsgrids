/**
 * The pieces every chart primitive shares — the value axis, the legend, the tooltip and the
 * screen-reader table. Internal: an app composes `LineChart`, `BarChart`, `BarList` and
 * `DonutChart`, never these.
 */

import type { ReactNode } from "react";

import { cn } from "../lib/cn";
import type { Scale } from "../lib/chart";
import { yOf } from "../lib/chart";
import { SERIES_TONE } from "../lib/tones";
import type { Tone } from "../lib/tones";

/**
 * Every chart's text alternative: its data as a real table, off screen. A table and not a
 * sentence, because a screen reader can move through a table by row and column — "October,
 * outflow" — which is what reading a chart is.
 */
export function ChartDataTable({
  caption,
  headers,
  rows,
}: {
  caption: string;
  headers: string[];
  rows: { key: string; cells: string[] }[];
}) {
  return (
    // The off-screen clip sits on a wrapper, not the table: a table lays out at its content width
    // whatever `width: 1px` says, so `sr-only` on the table itself widened the page and gave every
    // chart screen a horizontal scrollbar on a phone.
    <div className="sr-only">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header} scope="col">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.key}>
              {row.cells.map((cell, index) =>
                index === 0 ? (
                  <th key={index} scope="row">
                    {cell}
                  </th>
                ) : (
                  <td key={index}>{cell}</td>
                ),
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Colour keys for the series, visible only. The data table carries the same names. */
export function ChartLegend({ items }: { items: { key: string; label: string; tone: Tone; value?: string | undefined }[] }) {
  return (
    <ul aria-hidden className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs font-semibold text-ink-soft">
      {items.map((item) => (
        <li key={item.key} className="inline-flex items-center gap-1.5">
          <span className={cn("size-2.5 rounded-full", SERIES_TONE[item.tone].swatch)} />
          {item.label}
          {item.value !== undefined ? <span className="tnum text-ink">{item.value}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/**
 * Horizontal gridlines with their values in the gutter to the left. HTML, not SVG text: the plot
 * is stretched to fit its box, and stretched text is unreadable.
 */
export function ValueAxis({ scale, format }: { scale: Scale; format: (value: number) => string }) {
  return (
    <div aria-hidden className="pointer-events-none absolute inset-0">
      {scale.ticks.map((tick) => (
        <div key={tick} className="absolute inset-x-0 border-t border-line" style={{ top: `${yOf(tick, scale)}%` }}>
          <span className="tnum absolute right-full mr-2 -translate-y-1/2 whitespace-nowrap text-xs text-muted">
            {format(tick)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** Category labels under the plot, thinned to every `stride`th so they never overlap. */
export function CategoryAxis({
  categories,
  stride,
  position,
}: {
  categories: string[];
  stride: number;
  /** Where a category's label is centred, as a percentage across the plot. */
  position: (index: number) => number;
}) {
  const last = categories.length - 1;
  // Every `stride`th label, and always the last — dropping a thinned one that would crowd it.
  const shown = (index: number) => index === last || (index % stride === 0 && last - index >= stride);
  return (
    <div aria-hidden className="relative mt-2 h-4">
      {categories.map((category, index) =>
        shown(index) ? (
          <span
            key={`${category}-${index}`}
            className={cn(
              "tnum absolute top-0 whitespace-nowrap text-xs text-muted",
              // The ends align inward so the first and last labels stay inside the card.
              position(index) <= 0 ? "" : position(index) >= 100 ? "-translate-x-full" : "-translate-x-1/2",
            )}
            style={{ left: `${position(index)}%` }}
          >
            {category}
          </span>
        ) : null,
      )}
    </div>
  );
}

/**
 * The reading for the category under the cursor, at the top of the plot beside the cursor line.
 * Inside the plot, not above it: a card's edge or the header above must never clip it.
 */
export function ChartTooltip({ left, children }: { left: number; children: ReactNode }) {
  return (
    <div
      aria-hidden
      className={cn(
        "pointer-events-none absolute top-0 z-10 flex flex-col gap-0.5 rounded-tile bg-ink px-3 py-1.5",
        "text-xs font-semibold whitespace-nowrap text-surface shadow-float",
        left < 20 ? "" : left > 80 ? "-translate-x-full" : "-translate-x-1/2",
      )}
      style={{ left: `${left}%` }}
    >
      {children}
    </div>
  );
}
