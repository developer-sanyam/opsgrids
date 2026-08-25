/**
 * What this product's "Sample data" chips say on hover — which service is not wired yet. The chip
 * is `@atmora/ui`'s `SampleChip`; only the sentence is ours.
 */
export const SAMPLE_DATA = {
  title:
    "Representative data — there is no reporting service for this trend yet",
} as const;

/**
 * For a widget whose headline number is live and only its delta and sparkline are stand-ins —
 * "Sample trend", not "Sample data".
 */
export const SAMPLE_TREND = {
  label: "Sample trend",
  title:
    "Representative data — there is no reporting service for this trend yet",
} as const;
