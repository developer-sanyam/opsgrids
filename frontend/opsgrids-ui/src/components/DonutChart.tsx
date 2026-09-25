import { arcs, defaultFormat, percentages } from "../lib/chart";
import { cn } from "../lib/cn";
import { SERIES_TONE, seriesTone } from "../lib/tones";
import type { Tone } from "../lib/tones";
import { ChartDataTable } from "./ChartParts";

export interface DonutSlice {
  key: string;
  label: string;
  value: number;
  /** Name it when the colour means something — Active in `positive`, Suspended in `danger`. */
  tone?: Tone | undefined;
}

interface DonutChartProps {
  /** What the whole is: "Accounts by status". Its table's caption. */
  label: string;
  /** Five or fewer; fold the tail into "Other". Past six the colours repeat. */
  slices: DonutSlice[];
  formatValue?: ((value: number) => string) | undefined;
  /** The figure in the hole. Default the formatted total. */
  centerValue?: string | undefined;
  /** The word under it. Default "Total". */
  centerLabel?: string | undefined;
  className?: string | undefined;
}

const RADIUS = 46;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

/**
 * Parts of a whole as a ring, with the total in the middle and a legend of value and share — the
 * five `strokeDasharray` donuts in the apps, drawn once.
 *
 * The legend is the reading; the ring is the shape of it. Both are hidden from assistive
 * technology in favour of an off-screen table (item, value, share), and shares are whole numbers
 * that add up to 100. A donut is for a few parts: for more than five, a `BarList` is clearer.
 */
export function DonutChart({
  label,
  slices,
  formatValue = defaultFormat,
  centerValue,
  centerLabel = "Total",
  className,
}: DonutChartProps) {
  const values = slices.map((slice) => slice.value);
  const total = values.reduce((sum, value) => sum + (value > 0 ? value : 0), 0);
  const ring = arcs(values);
  const shares = percentages(values);
  const tones = slices.map((slice, index) => seriesTone(index, slice.tone));

  return (
    <figure className={cn("m-0 flex flex-col items-center gap-6 sm:flex-row", className)}>
      <div aria-hidden className="relative size-36 shrink-0">
        <svg viewBox="0 0 120 120" className="size-full -rotate-90">
          <circle cx="60" cy="60" r={RADIUS} fill="none" strokeWidth="14" className="stroke-ground" />
          {ring.map((arc, index) =>
            arc.share > 0 ? (
              <circle
                key={slices[index]?.key ?? index}
                cx="60"
                cy="60"
                r={RADIUS}
                fill="none"
                strokeWidth="14"
                strokeDasharray={`${arc.share * CIRCUMFERENCE} ${CIRCUMFERENCE}`}
                strokeDashoffset={-arc.start * CIRCUMFERENCE}
                className={SERIES_TONE[tones[index] ?? "accent"].stroke}
              />
            ) : null,
          )}
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="tnum text-xl font-bold leading-tight text-ink">{centerValue ?? formatValue(total)}</span>
          <span className="text-xs text-muted">{centerLabel}</span>
        </div>
      </div>
      <ul aria-hidden className="flex w-full min-w-0 flex-1 flex-col gap-2.5 sm:max-w-sm">
        {slices.map((slice, index) => (
          <li key={slice.key} className="flex items-center gap-2 text-sm">
            <span className={cn("size-2.5 shrink-0 rounded-full", SERIES_TONE[tones[index] ?? "accent"].swatch)} />
            <span className="min-w-0 flex-1 truncate text-ink-soft">{slice.label}</span>
            <span className="tnum font-semibold text-ink">{formatValue(slice.value)}</span>
            <span className="tnum w-10 text-right text-muted">{shares[index]}%</span>
          </li>
        ))}
      </ul>
      <ChartDataTable
        caption={`${label}. ${centerLabel}: ${centerValue ?? formatValue(total)}`}
        headers={["Item", "Value", "Share"]}
        rows={slices.map((slice, index) => ({
          key: slice.key,
          cells: [slice.label, formatValue(slice.value), `${shares[index]}%`],
        }))}
      />
    </figure>
  );
}
