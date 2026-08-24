/**
 * Pure rollups over the deal list. No I/O, no React — just grouping, sums and percentages, so the
 * Overview KPIs, the by-stage funnel, the kanban columns and the "needs attention" list all derive
 * from one `GET /api/v1/deals` payload. Kept pure so it is trivially testable.
 */
import type { AttentionItem, Deal, DealStage, StageRollup } from "@/api/types";
import { OPEN_STAGES, STAGES, isOpen, stageLabel } from "./stages";

/** Deals still in flight (not closed-won or closed-lost). */
export function openDeals(deals: readonly Deal[]): Deal[] {
  return deals.filter((d) => isOpen(d.stage));
}

/** Total open pipeline value, in minor units. */
export function pipelineValueMinor(deals: readonly Deal[]): number {
  return openDeals(deals).reduce((sum, d) => sum + d.value_minor, 0);
}

/** Probability-weighted open pipeline (the forecast), in minor units. */
export function weightedPipelineMinor(deals: readonly Deal[]): number {
  return openDeals(deals).reduce(
    (sum, d) => sum + Math.round((d.value_minor * d.probability) / 100),
    0,
  );
}

/** How many deals are open. */
export function openCount(deals: readonly Deal[]): number {
  return openDeals(deals).length;
}

/**
 * Win rate over *decided* deals: closed-won / (closed-won + closed-lost), as a 0–100 percentage.
 * Deals still open don't count — a full pipeline shouldn't drag the rate toward zero.
 */
export function winRatePct(deals: readonly Deal[]): number {
  const won = deals.filter((d) => d.stage === "closed_won").length;
  const lost = deals.filter((d) => d.stage === "closed_lost").length;
  const decided = won + lost;
  return decided === 0 ? 0 : Math.round((won / decided) * 100);
}

/** Total value won across all closed-won deals, in minor units. */
export function wonValueMinor(deals: readonly Deal[]): number {
  return deals
    .filter((d) => d.stage === "closed_won")
    .reduce((sum, d) => sum + d.value_minor, 0);
}

/**
 * The by-stage funnel over the four open stages: count and summed value per stage, with `pct`
 * scaled to the richest stage so the bars read as a funnel.
 */
export function stageFunnel(deals: readonly Deal[]): StageRollup[] {
  const rows = OPEN_STAGES.map((stage) => {
    const inStage = deals.filter((d) => d.stage === stage);
    const value = inStage.reduce((sum, d) => sum + d.value_minor, 0);
    return { stage, label: stageLabel(stage), count: inStage.length, value_minor: value, pct: 0 };
  });
  const max = Math.max(...rows.map((r) => r.value_minor), 1);
  return rows.map((r) => ({ ...r, pct: Math.round((r.value_minor / max) * 100) }));
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

/** Count deals whose owner is the given id (all stages). */
export function dealsOwnedBy(deals: readonly Deal[], ownerId: string): number {
  return deals.filter((d) => d.owner_id === ownerId).length;
}
