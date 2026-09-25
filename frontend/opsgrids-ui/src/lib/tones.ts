/**
 * The semantic families a figure can be drawn in, by token name — the accent and the five status
 * families in `console.css`, never a sixth decorative colour. One list, so a KPI's icon tile and
 * a chart series in the same tone are the same colour, and a token re-value moves both.
 *
 * Class strings are written out whole: Tailwind finds utilities by scanning source text, and a
 * class assembled at runtime (`"stroke-" + tone`) is one it never generates.
 */

export type Tone = "accent" | "positive" | "info" | "cyan" | "warning" | "danger";

/** The pastel square behind a KPI or metric icon. */
export const ICON_TILE: Record<Tone, string> = {
  accent: "bg-accent-soft text-accent-ink",
  positive: "bg-present-soft text-present",
  info: "bg-info-soft text-info",
  cyan: "bg-cyan-soft text-cyan",
  warning: "bg-late-soft text-late",
  danger: "bg-danger-soft text-danger",
};

/** A chart series: its line, its fill, its legend swatch, and `currentColor` for its gradient. */
export const SERIES_TONE: Record<Tone, { stroke: string; fill: string; swatch: string; current: string }> = {
  accent: { stroke: "stroke-accent", fill: "fill-accent", swatch: "bg-accent", current: "text-accent" },
  info: { stroke: "stroke-info", fill: "fill-info", swatch: "bg-info", current: "text-info" },
  cyan: { stroke: "stroke-cyan", fill: "fill-cyan", swatch: "bg-cyan", current: "text-cyan" },
  positive: { stroke: "stroke-present", fill: "fill-present", swatch: "bg-present", current: "text-present" },
  warning: { stroke: "stroke-late", fill: "fill-late", swatch: "bg-late", current: "text-late" },
  danger: { stroke: "stroke-danger", fill: "fill-danger", swatch: "bg-danger", current: "text-danger" },
};

/** The order series take tones in when none is named: the accent first, then the most distinct. */
export const SERIES_ORDER: readonly Tone[] = ["accent", "info", "cyan", "positive", "warning", "danger"];

/** The tone for the series at `index`, unless it names its own. */
export function seriesTone(index: number, own: Tone | undefined): Tone {
  return own ?? SERIES_ORDER[index % SERIES_ORDER.length] ?? "accent";
}
