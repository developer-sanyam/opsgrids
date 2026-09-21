import { describe, expect, it } from "vitest";

import type { Campaign } from "@/api/types";
import { cac, costPerLead, needsAttention, roas } from "./derive";

function campaign(overrides: Partial<Campaign>): Campaign {
  return {
    id: "cmp-1",
    name: "Test campaign",
    channel: "search",
    status: "live",
    currency: "INR",
    budget_minor: 0,
    spent_minor: 0,
    revenue_minor: 0,
    pipeline_minor: 0,
    leads: 0,
    conversions: 0,
    goal: "",
    audience_id: "aud-1",
    start_date: "2026-01-01",
    end_date: null,
    owner: "",
    ...overrides,
  };
}

describe("roas", () => {
  it("is 0 rather than NaN or Infinity when there is no spend", () => {
    expect(roas(50000, 0)).toBe(0);
  });

  it("divides revenue by spend when there is spend", () => {
    expect(roas(400000, 100000)).toBe(4);
  });
});

describe("costPerLead / cac", () => {
  it("is null rather than Infinity when there are no leads", () => {
    expect(costPerLead(50000, 0)).toBeNull();
  });

  it("is null rather than Infinity when nothing converted", () => {
    expect(cac(50000, 0)).toBeNull();
  });
});

describe("needsAttention", () => {
  it("never reports 'Infinity% of budget spent' for a zero-budget campaign that is still spending", () => {
    // A campaign can be live, spending and revenue-healthy (ROAS >= 2.5) with no budget recorded
    // at all — `spent_minor / budget_minor` would be `Infinity` unguarded.
    const items = needsAttention([
      campaign({ status: "live", budget_minor: 0, spent_minor: 10000, revenue_minor: 40000 }),
    ]);
    expect(items).toHaveLength(0);
  });

  it("still flags a real budget-exhaustion case", () => {
    const items = needsAttention([
      campaign({ status: "live", budget_minor: 100000, spent_minor: 95000, revenue_minor: 200000 }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]?.reason).toContain("of budget spent");
  });

  it("flags a low-ROAS campaign regardless of budget", () => {
    const items = needsAttention([
      campaign({ status: "live", spent_minor: 100000, revenue_minor: 100000 }),
    ]);
    expect(items).toHaveLength(1);
    expect(items[0]?.reason).toContain("below the 2× floor");
  });

  it("excludes a draft or ended campaign — nothing to act on", () => {
    const items = needsAttention([
      campaign({ status: "draft", spent_minor: 0, revenue_minor: 0 }),
      campaign({ status: "ended", spent_minor: 100000, revenue_minor: 50000 }),
    ]);
    expect(items).toHaveLength(0);
  });
});
