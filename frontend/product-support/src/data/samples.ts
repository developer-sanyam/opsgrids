/**
 * The week-over-week delta and sparkline beside each KPI on the Overview — the one thing
 * `GET /support/overview` does not return (it answers the current snapshot only, not a trailing
 * history). The headline numbers themselves, and the volume chart, come from the live endpoint
 * (`api/client.ts#getOverview`); only these two stay representative, and only while `TRENDS_ARE_SAMPLE`
 * is true, behind `SampleChip` so they can never be mistaken for the organization's real trend.
 */

/** Seven-day sparkline series, one per KPI card, oldest → newest. */
export const KPI_TREND = {
  open: [11, 12, 10, 13, 12, 15, 14],
  breaching: [5, 6, 4, 5, 3, 4, 3],
  firstResponse: [31, 29, 33, 28, 30, 26, 27],
  csat: [78, 80, 77, 81, 82, 79, 80],
} as const;

/** Week-over-week change for each KPI (percent). Sign is the real direction; the card decides colour. */
export const KPI_DELTA = {
  open: 6.1,
  breaching: -18.0,
  firstResponse: -8.0,
  csat: 1.9,
} as const;
