import { describe, expect, it } from "vitest";

import type { Deal } from "@/api/types";
import * as derive from "./derive";
import { dealsByStage, needsAttention, openDeals } from "./derive";

/**
 * What is left of the deal rollups now that the Overview reads `GET /deals/summary`: the open-deal
 * filter, the board's grouping and the "needs attention" list. The KPIs and the funnel are the
 * server's, so the rollups that duplicated them are gone, and the export list is pinned so a dead
 * copy cannot quietly come back. (The rebuild of Overview and Deals deletes this file with
 * `derive.ts`.)
 */

function deal(over: Partial<Deal>): Deal {
  return {
    id: "deal-1",
    name: "Fleet telematics rollout",
    company_id: "comp-1",
    primary_contact_id: null,
    value_minor: 100_000,
    currency: "INR",
    stage: "lead",
    owner_id: "own-1",
    probability: 20,
    close_date: "2026-12-31",
    created_at: "2026-08-01T00:00:00Z",
    last_activity_at: null,
    source: "Referral",
    ...over,
  };
}

describe("the export list", () => {
  it("is the three rollups still in use", () => {
    expect(Object.keys(derive).sort()).toEqual(["dealsByStage", "needsAttention", "openDeals"]);
  });
});

describe("openDeals", () => {
  it("keeps the four open stages and drops the two closed ones", () => {
    const deals = [
      deal({ id: "a", stage: "lead" }),
      deal({ id: "b", stage: "negotiation" }),
      deal({ id: "c", stage: "closed_won" }),
      deal({ id: "d", stage: "closed_lost" }),
    ];
    expect(openDeals(deals).map((d) => d.id)).toEqual(["a", "b"]);
  });
});

describe("dealsByStage", () => {
  it("has a column for every stage, in board order, richest deal first", () => {
    const grouped = dealsByStage([
      deal({ id: "small", stage: "proposal", value_minor: 10 }),
      deal({ id: "big", stage: "proposal", value_minor: 99 }),
      deal({ id: "won", stage: "closed_won" }),
    ]);
    expect(Object.keys(grouped)).toEqual([
      "lead",
      "qualified",
      "proposal",
      "negotiation",
      "closed_won",
      "closed_lost",
    ]);
    expect(grouped.proposal.map((d) => d.id)).toEqual(["big", "small"]);
    expect(grouped.closed_won.map((d) => d.id)).toEqual(["won"]);
    expect(grouped.lead).toEqual([]);
  });
});

describe("needsAttention", () => {
  const today = "2026-09-20";

  it("orders overdue, then closing soon, then stale, and says why", () => {
    const items = needsAttention(
      [
        deal({ id: "stale", close_date: "2026-12-31", last_activity_at: "2026-08-30T00:00:00Z" }),
        deal({ id: "soon", close_date: "2026-09-25" }),
        deal({ id: "late", close_date: "2026-09-10" }),
      ],
      today,
    );
    expect(items.map((i) => [i.deal.id, i.reason, i.detail])).toEqual([
      ["late", "overdue", "Close date 10d overdue"],
      ["soon", "closing_soon", "Closes in 5d"],
      ["stale", "stale", "No activity in 21d"],
    ]);
  });

  it("says a deal closing today closes today, and ignores closed deals", () => {
    const items = needsAttention(
      [
        deal({ id: "today", close_date: today }),
        deal({ id: "won", stage: "closed_won", close_date: "2026-09-01" }),
      ],
      today,
    );
    expect(items.map((i) => [i.deal.id, i.detail])).toEqual([["today", "Closes today"]]);
  });

  it("puts the larger deal first within one reason", () => {
    const items = needsAttention(
      [
        deal({ id: "small", close_date: "2026-09-25", value_minor: 1 }),
        deal({ id: "large", close_date: "2026-09-26", value_minor: 9 }),
      ],
      today,
    );
    expect(items.map((i) => i.deal.id)).toEqual(["large", "small"]);
  });
});
