/**
 * What is left of the pure rollups over the deal list: the open-deal filter, the kanban columns and
 * the "needs attention" list. The Overview's KPIs and funnel are `GET /deals/summary` now, so the
 * client-side copies of them are gone. No I/O, no React, so it is trivially testable.
 */
import type { AttentionItem, Deal, DealStage } from "@/api/types";
import { STAGES, isOpen } from "./stages";

/** Deals still in flight (not closed-won or closed-lost). */
export function openDeals(deals: readonly Deal[]): Deal[] {
  return deals.filter((d) => isOpen(d.stage));
}

/** Every deal grouped by stage, in board order — the kanban columns (all six stages). */
export function dealsByStage(deals: readonly Deal[]): Record<DealStage, Deal[]> {
  const groups = Object.fromEntries(STAGES.map((s) => [s.stage, [] as Deal[]])) as Record<
    DealStage,
    Deal[]
  >;
  for (const deal of deals) groups[deal.stage].push(deal);
  for (const stage of STAGES) {
    groups[stage.stage].sort((a, b) => b.value_minor - a.value_minor);
  }
  return groups;
}

/** Whole days from `fromIso` to `toIso` (both `YYYY-MM-DD…`). Positive when `to` is later. */
function daysBetween(fromIso: string, toIso: string): number {
  const from = Date.parse(fromIso.slice(0, 10));
  const to = Date.parse(toIso.slice(0, 10));
  return Math.round((to - from) / 86_400_000);
}

const CLOSING_SOON_DAYS = 10;
const STALE_DAYS = 14;

/**
 * Open deals that need a nudge, most urgent first: overdue close dates, then deals closing within
 * ten days, then deals with no activity in the last fortnight. `today` keeps this pure.
 */
export function needsAttention(deals: readonly Deal[], today: string): AttentionItem[] {
  const items: AttentionItem[] = [];
  for (const deal of openDeals(deals)) {
    const toClose = daysBetween(today, deal.close_date);
    if (toClose < 0) {
      items.push({ deal, reason: "overdue", detail: `Close date ${-toClose}d overdue` });
      continue;
    }
    if (toClose <= CLOSING_SOON_DAYS) {
      items.push({
        deal,
        reason: "closing_soon",
        detail: toClose === 0 ? "Closes today" : `Closes in ${toClose}d`,
      });
      continue;
    }
    if (deal.last_activity_at) {
      const idle = daysBetween(deal.last_activity_at, today);
      if (idle >= STALE_DAYS) {
        items.push({ deal, reason: "stale", detail: `No activity in ${idle}d` });
      }
    }
  }
  const rank: Record<AttentionItem["reason"], number> = { overdue: 0, closing_soon: 1, stale: 2 };
  return items.sort(
    (a, b) => rank[a.reason] - rank[b.reason] || b.deal.value_minor - a.deal.value_minor,
  );
}
