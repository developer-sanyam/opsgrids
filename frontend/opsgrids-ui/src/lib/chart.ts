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
 *
 * `integer: true` is for a series that counts things — accounts, trials, seats — where
 * `niceStep` would otherwise land on a fractional step (0.5, 0.2…) whenever the range is small.
 * Half an account is never a real gridline, so this rounds the step up to the nearest whole
 * number instead. A range wide enough for `niceStep` to already pick a whole number is unaffected.
 */
export function scaleFor(values: readonly number[], intervals = 3, integer = false): Scale {
  const finite = values.filter((value) => Number.isFinite(value));
  const high = Math.max(0, ...finite);
  const low = Math.min(0, ...finite);
  if (high === 0 && low === 0) return { min: 0, max: intervals, ticks: Array.from({ length: intervals + 1 }, (_, i) => i) };
  const rawStep = niceStep((high - low) / intervals);
  const step = integer ? Math.max(1, Math.round(rawStep)) : rawStep;
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

/**
 * The same line, softened: a quadratic curve through the midpoint of each pair of points rather
 * than a straight segment between them, so a handful of small monthly counts reads as a gentle
 * trend instead of a jagged EKG line. Each interior point is still where the value says it is —
 * only the join between two points bows toward it rather than snapping straight there — and the
 * curve finishes with a plain line into the last point, which is exact.
 */
export function smoothLinePath(values: readonly number[], scale: Scale): string {
  if (values.length === 0) return "";
  const points = values.map((value, index) => ({ x: xOf(index, values.length), y: yOf(value, scale) }));
  const first = points[0];
  if (!first || points.length < 3) return linePath(values, scale);
  let d = `M${first.x.toFixed(2)},${first.y.toFixed(2)}`;
  for (let i = 0; i < points.length - 1; i += 1) {
    const point = points[i];
    const next = points[i + 1];
    if (!point || !next) continue;
    const midX = (point.x + next.x) / 2;
    const midY = (point.y + next.y) / 2;
    d += ` Q${point.x.toFixed(2)},${point.y.toFixed(2)} ${midX.toFixed(2)},${midY.toFixed(2)}`;
  }
  const last = points[points.length - 1];
  if (last) d += ` L${last.x.toFixed(2)},${last.y.toFixed(2)}`;
  return d;
}

/** `smoothLinePath`, closed down to the zero line, for a soft area fill. */
export function smoothAreaPath(values: readonly number[], scale: Scale): string {
  if (values.length === 0) return "";
  const base = yOf(Math.max(scale.min, 0), scale).toFixed(2);
  const last = xOf(values.length - 1, values.length).toFixed(2);
  const first = xOf(0, values.length).toFixed(2);
  return `${smoothLinePath(values, scale)} L${last},${base} L${first},${base} Z`;
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

/**
 * The smallest stride (every nth label, and always the last) at which no two printed category
 * labels come closer than `gap` px, given each label's measured width and where it sits.
 *
 * `labelStride` guesses from the count alone, which is right for "Mon Tue Wed" and wrong for
 * "September 2025" on a wide card, where six labels of that length still touch. This takes the
 * widths the browser actually laid out. The first label sits flush left, the last flush right and
 * every other one is centred on its point — the same alignment `CategoryAxis` draws.
 *
 * With no measurements (a width of 0, as in a test renderer) it returns `from`, so nothing moves.
 */
export function strideThatFits(
  widths: readonly number[],
  positions: readonly number[],
  containerWidth: number,
  from: number,
  gap = 8,
): number {
  const count = widths.length;
  if (count < 2 || containerWidth <= 0) return from;
  const last = count - 1;
  const extent = (index: number): [number, number] => {
    const width = widths[index] ?? 0;
    const at = positions[index] ?? 0;
    const x = (at / 100) * containerWidth;
    if (at <= 0) return [x, x + width];
    if (at >= 100) return [x - width, x];
    return [x - width / 2, x + width / 2];
  };
  for (let stride = Math.max(1, from); stride <= count; stride += 1) {
    const shown: number[] = [];
    for (let index = 0; index < count; index += 1) {
      if (index === last || (index % stride === 0 && last - index >= stride)) shown.push(index);
    }
    let fits = true;
    for (let i = 1; i < shown.length && fits; i += 1) {
      const [, previousEnd] = extent(shown[i - 1] ?? 0);
      const [nextStart] = extent(shown[i] ?? 0);
      fits = nextStart - previousEnd >= gap;
    }
    if (fits) return stride;
  }
  return count;
}

/** The default number format: grouped, at most one decimal. Charts of money pass their own. */
export const defaultFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format;
