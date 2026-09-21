/**
 * Representative (non-live) series for the Overview surfaces with no historical service yet: the
 * pipeline-value-over-time chart and the KPI sparklines. Every widget that reads from here renders
 * the shared `SampleChip` so a sample trend is never mistaken for a live figure. The live headline
 * numbers still come from the deal list via `derive.ts`; only the *history* is sample.
 */
import type { TrendPoint } from "@/api/types";

/** Rupees -> paise, matching the fixtures' minor-unit convention. */
const rupees = (n: number): number => n * 100;

/**
 * Open pipeline value at each month-end through 2026, rising to roughly today's live open
 * pipeline. Minor units of INR.
 */
export const PIPELINE_TREND: TrendPoint[] = [
  { label: "Jan", value_minor: rupees(172_000_000) },
  { label: "Feb", value_minor: rupees(188_000_000) },
  { label: "Mar", value_minor: rupees(196_000_000) },
  { label: "Apr", value_minor: rupees(214_000_000) },
  { label: "May", value_minor: rupees(221_000_000) },
  { label: "Jun", value_minor: rupees(238_000_000) },
  { label: "Jul", value_minor: rupees(251_000_000) },
  { label: "Aug", value_minor: rupees(263_000_000) },
];

/** Percent change to show beside the pipeline headline (representative). */
export const PIPELINE_DELTA = 6.4;

/**
 * Small trend series and deltas for the four Overview KPI sparklines. Representative, not live —
 * every `KpiCard` that reads these passes a `sample` prop naming so, because there is no history
 * service behind them yet. The headline figure beside each one is always the live one.
 */
export const KPI_TRENDS = {
  pipeline: { series: [172, 188, 196, 214, 221, 238, 263], deltaPct: 6.4 },
  openDeals: { series: [11, 12, 13, 12, 14, 15, 15], deltaPct: 3.1 },
  winRate: { series: [58, 60, 61, 59, 62, 63, 67], deltaPct: 4.8 },
  activities: { series: [22, 26, 24, 29, 31, 28, 34], deltaPct: -2.4 },
} as const;
