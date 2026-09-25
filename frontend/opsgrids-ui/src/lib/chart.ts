/**
 * The arithmetic behind the chart primitives, kept pure so it is tested without a DOM: a value
 * axis that lands on round numbers, points in a 0–100 box, and the arcs of a donut.
 *
 * Plots are drawn in a 0–100 square stretched to the plot's size (`preserveAspectRatio="none"`),
 * so a point's coordinates are also its percentage position — which is how the HTML labels,
 * dots and tooltip line up with the SVG without measuring anything.
 */

/** Round a step up to 1, 2, 2.5 or 5 times a power of ten: axes people can read at a glance. */
export function niceStep(raw: number): number {
  if (!Number.isFinite(raw) || raw <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(raw));
  const normalised = raw / magnitude;
  const nice = normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 2.5 ? 2.5 : normalised <= 5 ? 5 : 10;
  return nice * magnitude;
}

export interface Scale {
  min: number;
  max: number;
  /** Gridline values, bottom to top, `min` and `max` included. */
  ticks: number[];
}

/**
 * The value axis for a set of numbers: from zero (or below it, for a negative value) to the first
 * round number at or above the largest, in steps of about a third of the range. Zero is always on
 * the axis — a bar or an area measured from anywhere else misstates every value on it.
 */
export function scaleFor(values: readonly number[], intervals = 3): Scale {
  const finite = values.filter((value) => Number.isFinite(value));
  const high = Math.max(0, ...finite);
  const low = Math.min(0, ...finite);
  if (high === 0 && low === 0) return { min: 0, max: intervals, ticks: Array.from({ length: intervals + 1 }, (_, i) => i) };
  const step = niceStep((high - low) / intervals);
  const min = low < 0 ? -Math.ceil(-low / step) * step : 0;
  const count = Math.max(1, Math.ceil((high - min) / step));
  const ticks = Array.from({ length: count + 1 }, (_, i) => min + i * step);
  return { min, max: ticks[ticks.length - 1] ?? high, ticks };
}

/** A value's height in the 0–100 box, measured from the top (SVG's y runs downward). */
export function yOf(value: number, scale: Scale): number {
  const span = scale.max - scale.min || 1;
  return 100 - ((value - scale.min) / span) * 100;
}

/** The x of the `index`th of `count` points, spread edge to edge. A lone point sits in the middle. */
export function xOf(index: number, count: number): number {
  return count <= 1 ? 50 : (index / (count - 1)) * 100;
}

/** An SVG path through the points, or an empty string for no points. */
export function linePath(values: readonly number[], scale: Scale): string {
  return values
    .map((value, index) => `${index === 0 ? "M" : "L"}${xOf(index, values.length).toFixed(2)},${yOf(value, scale).toFixed(2)}`)
    .join(" ");
}

/** The line closed down to the zero line, for the fill under an area chart. */
export function areaPath(values: readonly number[], scale: Scale): string {
  if (values.length === 0) return "";
  const base = yOf(Math.max(scale.min, 0), scale).toFixed(2);
  const last = xOf(values.length - 1, values.length).toFixed(2);
  const first = xOf(0, values.length).toFixed(2);
  return `${linePath(values, scale)} L${last},${base} L${first},${base} Z`;
}

export interface Arc {
  /** Share of the whole, 0–1. */
  share: number;
  /** Where the arc starts, as a share of the circle, 0–1, clockwise from twelve o'clock. */
  start: number;
}

/**
 * The arcs of a donut. Negative and non-finite values count as zero — a share of a whole cannot
 * be negative — and an all-zero set has no arcs rather than dividing by zero.
 */
export function arcs(values: readonly number[]): Arc[] {
  const clean = values.map((value) => (Number.isFinite(value) && value > 0 ? value : 0));
  const total = clean.reduce((sum, value) => sum + value, 0);
  if (total === 0) return clean.map(() => ({ share: 0, start: 0 }));
  let start = 0;
  return clean.map((value) => {
    const arc = { share: value / total, start };
    start += value / total;
    return arc;
  });
}

/** Whole-number percentages that add up to 100, largest remainder first — no "99%" legends. */
export function percentages(values: readonly number[]): number[] {
  const shares = arcs(values).map((arc) => arc.share * 100);
  const floors = shares.map(Math.floor);
  const total = floors.reduce((sum, value) => sum + value, 0);
  if (shares.every((share) => share === 0)) return floors;
  const order = shares
    .map((share, index) => ({ index, remainder: share - Math.floor(share) }))
    .sort((a, b) => b.remainder - a.remainder);
  for (let i = 0; i < 100 - total; i += 1) {
    const target = order[i % order.length];
    if (target) floors[target.index] = (floors[target.index] ?? 0) + 1;
  }
  return floors;
}

/** Which labels to print along an axis of `count` categories so they never crowd: every nth. */
export function labelStride(count: number, room = 6): number {
  return Math.max(1, Math.ceil(count / room));
}

/** The default number format: grouped, at most one decimal. Charts of money pass their own. */
export const defaultFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format;
