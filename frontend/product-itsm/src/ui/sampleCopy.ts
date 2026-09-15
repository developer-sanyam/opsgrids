/**
 * What this product's "Sample data" chips say on hover — which service is not wired yet. The chip
 * is `@opsgrids/ui`'s `SampleChip`; only the sentence is ours.
 */
export const SAMPLE_DATA = {
  title:
    "Representative data — the reporting service isn't wired to this org yet",
} as const;

/**
 * For a widget whose headline number is live and only its delta and sparkline are stand-ins —
 * "Sample trend", not "Sample data".
 */
export const SAMPLE_TREND = {
  label: "Sample trend",
  title:
    "Representative data — the reporting service isn't wired to this org yet",
} as const;
