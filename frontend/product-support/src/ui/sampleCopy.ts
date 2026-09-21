/**
 * What this product's "Sample data" chip says on hover — which service is not wired yet. The
 * chip is `@opsgrids/ui`'s `SampleChip`; only the sentence is ours.
 *
 * Every KPI's headline number is live (`GET /support/overview`); only the delta and sparkline
 * beside it have no history endpoint to draw from, so this is the one variant this app still
 * needs — "Sample trend", not "Sample data".
 */
export const SAMPLE_TREND = {
  label: "Sample trend",
  title:
    "Representative data — there is no reporting service for this trend yet",
} as const;
