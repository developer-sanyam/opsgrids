/**
 * Trend history for the Overview — the one thing a current-snapshot query can't give you. The KPI
 * headline numbers and every breakdown are derived live from the ticket fixtures (`derive.ts`);
 * these arrays only supply the last seven days of context behind each headline (sparklines, the
 * volume chart, and the week-over-week delta pills).
 */
import type { VolumePoint } from "@/api/types";

/** Seven-day sparkline series, one per KPI card, oldest → newest (newest matches today's derived value). */
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

/** Tickets opened vs solved over the trailing week (ending on the fixed "today", a Monday). */
export const TICKET_VOLUME: VolumePoint[] = [
  { label: "Tue", date: "2026-08-18", opened: 34, solved: 31 },
  { label: "Wed", date: "2026-08-19", opened: 41, solved: 36 },
  { label: "Thu", date: "2026-08-20", opened: 38, solved: 42 },
  { label: "Fri", date: "2026-08-21", opened: 45, solved: 40 },
  { label: "Sat", date: "2026-08-22", opened: 16, solved: 19 },
  { label: "Sun", date: "2026-08-23", opened: 13, solved: 15 },
  { label: "Mon", date: "2026-08-24", opened: 29, solved: 24 },
];
