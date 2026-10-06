import { describe, expect, it } from "vitest";

import type { PlanChangePreview, PlanOption, PlanRef, Subscription } from "@/api/types";

import { formatDate } from "./money";
import {
  cancelBody,
  changePlanUnavailableReason,
  confirmLabel,
  failureMessage,
  optionDescription,
  optionLabels,
  pendingLine,
  previewNotes,
  previewSentence,
  priceLine,
  refusalGuidance,
  statusLine,
  uniqueOptions,
} from "./subscriptionCopy";

const NOW = new Date("2026-10-06T06:00:00Z");

function sub(overrides: Partial<Subscription>): Subscription {
  return {
    id: "s1",
    product_key: "crm",
    plan_version_id: "v1",
    state: "active",
    trial_ends_at: null,
    current_period_start: "2026-10-05T00:00:00Z",
    current_period_end: "2026-11-04T00:00:00Z",
    cancel_at: null,
    pending_plan_version_id: null,
    currency: "INR",
    entitled_products: ["crm"],
    items: [],
    dunning: null,
    ...overrides,
  };
}

function ref(overrides: Partial<PlanRef> = {}): PlanRef {
  return {
    plan_version_id: "v1",
    plan_id: "p1",
    plan_name: "Revenue Growth",
    version_no: 1,
    billing_interval: "month",
    negotiated: false,
    amount_visible: false,
    currency: null,
    recurring_minor: null,
    prices: null,
    ...overrides,
  };
}

function option(overrides: Partial<PlanOption> = {}): PlanOption {
  return { ...ref(), is_current: false, is_pending: false, blocked_code: null, blocked_reason: null, ...overrides };
}

function preview(overrides: Partial<PlanChangePreview> = {}): PlanChangePreview {
  return {
    expected: {
      state: "active",
      plan_version_id: "v1",
      pending_plan_version_id: null,
      current_period_end: "2026-11-04T00:00:00Z",
      lifecycle_claim_id: null,
    },
    action: "switch",
    direction: "upgrade",
    effective: "now",
    effective_at: null,
    would_clear_pending: false,
    entitled_added: [],
    entitled_removed: [],
    usage_priced: false,
    usage_closed_now: false,
    paid: true,
    prorated_charge_now: true,
    refusal_code: null,
    refusal_detail: null,
    ...overrides,
  };
}

describe("statusLine — the one date that matters next, by state", () => {
  it("an active subscription renews; it never shows a converted trial's end date", () => {
    const line = statusLine(sub({ state: "active", trial_ends_at: "2026-10-05T00:00:00Z" }), NOW);
    expect(line.primary).toBe(`Renews ${formatDate("2026-11-04T00:00:00Z")}`);
    expect(line.primary).not.toMatch(/trial|0 days/i);
    expect(line.secondary).toBeNull();
  });

  it.each([
    ["2026-10-20T06:00:00Z", "14 days left", false],
    ["2026-10-07T05:00:00Z", "1 day left", true],
    ["2026-10-08T06:00:00Z", "2 days left", true],
  ])("a trial ending %s says %s", (endsAt, left, attention) => {
    const line = statusLine(sub({ state: "trial", trial_ends_at: endsAt }), NOW);
    expect(line.primary).toBe(`Trial ends ${formatDate(endsAt)}`);
    expect(line.secondary).toBe(left);
    expect(line.attention).toBe(attention);
  });

  it("a trial past its end that the billing run has not converted yet says so, not '(0 days)'", () => {
    const line = statusLine(sub({ state: "trial", trial_ends_at: "2026-10-06T01:00:00Z" }), NOW);
    expect(line.primary).toBe(`Trial ended ${formatDate("2026-10-06T01:00:00Z")}`);
    expect(line.secondary).toBeNull();
  });

  it.each([
    ["past_due", null, "Awaiting payment", "Pay the overdue invoice to keep access"],
    // Dunning's cycle is closed by the time it suspends, so the row cannot say which suspension is
    // in force: both ways back are named, paying first.
    ["suspended", null, "Access suspended", "Pay any overdue invoice, or contact info@opsgrids.com to restore access"],
  ])("%s is stated in words", (state, dunning, primary, secondary) => {
    const line = statusLine(sub({ state, dunning }), NOW);
    expect([line.primary, line.secondary, line.attention]).toEqual([primary, secondary, true]);
  });

  it("an active subscription whose period ended and has not yet renewed says the renewal is due", () => {
    const line = statusLine(sub({ state: "active", current_period_end: "2026-10-04T00:00:00Z" }), NOW);
    expect(line.primary).toBe(`Renewal due ${formatDate("2026-10-04T00:00:00Z")}`);
    expect(line.secondary).toBe("Being processed");
  });

  it("a cancelled subscription says when it ended", () => {
    const line = statusLine(sub({ state: "cancelled", cancel_at: "2026-10-01T00:00:00Z" }), NOW);
    expect(line.primary).toBe(`Cancelled ${formatDate("2026-10-01T00:00:00Z")}`);
  });
});

describe("priceLine — an amount only where the server sent one", () => {
  it("a public plan shows its cadence, never a price", () => {
    expect(priceLine(ref({ billing_interval: "month" }))).toBe("Billed monthly");
    expect(priceLine(ref({ billing_interval: "year" }))).toBe("Billed yearly");
  });

  it("a negotiated price is formatted from integer minor units", () => {
    const line = priceLine(
      ref({
        amount_visible: true,
        currency: "INR",
        recurring_minor: 250000,
        prices: [{ kind: "flat", amount_minor: 250000, unit: null, unit_price_micros: null, meter_key: null }],
      }),
    );
    expect(line).toBe("₹2,500.00 per month");
  });

  it("per-seat and usage components read as such", () => {
    const line = priceLine(
      ref({
        amount_visible: true,
        currency: "USD",
        billing_interval: "year",
        prices: [
          { kind: "per_seat", amount_minor: 1200, unit: "seat", unit_price_micros: null, meter_key: null },
          { kind: "usage", amount_minor: null, unit: "call", unit_price_micros: 5000, meter_key: "api" },
        ],
      }),
    );
    expect(line).toMatch(/^\$12\.00 per seat per year \+ usage$/);
  });

  it("a zero-exponent currency keeps its own minor units", () => {
    const line = priceLine(
      ref({
        amount_visible: true,
        currency: "JPY",
        prices: [{ kind: "flat", amount_minor: 5000, unit: null, unit_price_micros: null, meter_key: null }],
      }),
    );
    expect(line).toMatch(/5,000 per month$/);
  });
});

describe("the picker's labels", () => {
  it("adds the version only where two plans share a name", () => {
    const labels = optionLabels([
      option({ plan_version_id: "a", plan_name: "Revenue Growth", version_no: 2 }),
      option({ plan_version_id: "b", plan_name: "Revenue Growth", version_no: 3, negotiated: true }),
      option({ plan_version_id: "c", plan_name: "Revenue", version_no: 1 }),
    ]);
    expect([...labels.values()]).toEqual(["Revenue Growth · v2", "Revenue Growth · v3", "Revenue"]);
  });

  it("adds the cadence where name and version both collide", () => {
    const labels = optionLabels([
      option({ plan_version_id: "a", plan_name: "Growth", version_no: 1, billing_interval: "month" }),
      option({ plan_version_id: "b", plan_name: "Growth", version_no: 1, billing_interval: "year" }),
    ]);
    expect([...labels.values()]).toEqual(["Growth · v1 · monthly", "Growth · v1 · yearly"]);
  });

  it("keeps each version once", () => {
    const once = uniqueOptions([option({ plan_version_id: "a" }), option({ plan_version_id: "a" })]);
    expect(once).toHaveLength(1);
  });

  it("describes a blocked plan by its reason and a negotiated one as agreed", () => {
    expect(optionDescription(option({ blocked_code: "cadence", blocked_reason: "Billed yearly." }))).toBe("Billed yearly.");
    expect(optionDescription(option({ negotiated: true }))).toBe("Billed monthly · agreed for your organisation");
    expect(optionDescription(option({ blocked_code: "current", blocked_reason: "This is the plan you are on." }))).toBe(
      "Billed monthly",
    );
  });
});

describe("previewSentence — what the server said, never 'immediately' by default", () => {
  const base = { product: "Revenue", fromPlan: "Growth", toPlan: "Scale", pendingPlan: null, pendingAt: null };

  it("a paid downgrade waits for the period end, with no credit", () => {
    const text = previewSentence({
      ...base,
      toPlan: "Lite",
      preview: preview({
        direction: "downgrade",
        effective: "next_period",
        effective_at: "2026-11-04T00:00:00Z",
        prorated_charge_now: false,
      }),
    });
    expect(text).toBe(
      `Revenue stays on Growth until ${formatDate("2026-11-04T00:00:00Z")}, then moves to Lite (a lower price). Nothing changes before then, and there is no refund or credit for the rest of the current period.`,
    );
    expect(confirmLabel(preview({ effective: "next_period" }))).toBe("Schedule change");
  });

  it("a paid upgrade moves now and says the difference is invoiced", () => {
    const text = previewSentence({ ...base, preview: preview() });
    expect(text).toMatch(/^Revenue moves from Growth to Scale now \(a higher price\)\./);
    expect(text).toMatch(/invoiced separately/);
    expect(confirmLabel(preview())).toBe("Change plan");
  });

  it("a trial change moves now and charges nothing", () => {
    const text = previewSentence({ ...base, preview: preview({ paid: false, prorated_charge_now: false }) });
    expect(text).toBe("Revenue moves from Growth to Scale now. You are on a trial, so nothing is charged.");
  });

  it("an equal-price change that closes usage says so instead of 'nothing is charged'", () => {
    const text = previewSentence({
      ...base,
      preview: preview({ direction: "equal", prorated_charge_now: false, usage_closed_now: true }),
    });
    expect(text).toMatch(/usage so far this period is invoiced at Growth's usage rates/);
    expect(text).not.toMatch(/Nothing extra is charged/);
  });

  it("withdrawing a scheduled change keeps the plan in force", () => {
    const text = previewSentence({
      ...base,
      pendingPlan: "Lite",
      pendingAt: "2026-11-04T00:00:00Z",
      preview: preview({ action: "withdraw_scheduled", direction: null }),
    });
    expect(text).toBe(
      `Revenue stays on Growth. The change to Lite scheduled on ${formatDate("2026-11-04T00:00:00Z")} is cancelled. Nothing is charged.`,
    );
  });

  it("notes what it replaces, what it adds and removes, and the access lag", () => {
    const notes = previewNotes(
      preview({ would_clear_pending: true, entitled_added: ["finance"], entitled_removed: ["hrms"] }),
      { pendingPlan: "Lite", productNames: (keys) => keys.map((k) => k.toUpperCase()) },
    );
    expect(notes).toEqual([
      "This replaces the change to Lite that was scheduled.",
      "Adds access to FINANCE.",
      "Removes access to HRMS.",
      "Product access updates within about 15 minutes.",
    ]);
  });
});

describe("the rest of the page's words", () => {
  it("a scheduled change names the plan and the date", () => {
    expect(pendingLine(ref({ plan_name: "Revenue" }), "2026-11-04T00:00:00Z")).toBe(
      `Changes to Revenue on ${formatDate("2026-11-04T00:00:00Z")}`,
    );
    expect(pendingLine(null, null)).toBeNull();
  });

  it("explains why Change plan is unavailable", () => {
    expect(changePlanUnavailableReason(sub({ state: "past_due" }))).toMatch(/overdue invoice/);
    expect(changePlanUnavailableReason(sub({ entitled_products: ["hrms", "crm"], product_key: "suite" }))).toMatch(
      /bundle/,
    );
    expect(changePlanUnavailableReason(sub({ state: "trial" }))).toBeNull();
  });

  it("the cancel confirm says it is immediate and final, by state", () => {
    const paid = cancelBody("Revenue", sub({ state: "active" }), "Lite");
    expect(paid).toMatch(/immediate and final/);
    expect(paid).toMatch(/can't be started again/);
    expect(paid).toMatch(/no automatic refund/);
    expect(paid).toMatch(/The scheduled change to Lite will not happen\.$/);
    const trial = cancelBody("Revenue", sub({ state: "trial" }), null);
    expect(trial).toMatch(/^The Revenue trial ends now/);
    expect(trial).toMatch(/can't be undone/);
  });

  it("a past-due cancel says the overdue invoice is still owed, and never offers a refund for time left", () => {
    const text = cancelBody("Revenue", sub({ state: "past_due" }), null);
    expect(text).toMatch(/The overdue invoice is still due after cancelling/);
    expect(text).not.toMatch(/refund for the time left/);
  });

  it("a suspended cancel says access has already ended and any overdue invoice stays due", () => {
    const text = cancelBody("Revenue", sub({ state: "suspended" }), null);
    expect(text).toMatch(/^Access to Revenue is already suspended; cancelling makes that final\./);
    expect(text).toMatch(/Any overdue invoice is still due after cancelling/);
    expect(text).not.toMatch(/ends now/);
  });

  it("a scheduled change whose date has passed takes effect before the cancel, not 'will not happen'", () => {
    const text = cancelBody("Revenue", sub({ state: "active" }), "Lite", "2026-10-04T00:00:00Z", NOW);
    expect(text).toMatch(new RegExp(`The change to Lite was due on ${formatDate("2026-10-04T00:00:00Z")}, so it takes effect before the cancellation\\.$`));
    expect(text).not.toMatch(/will not happen/);
    const future = cancelBody("Revenue", sub({ state: "active" }), "Lite", "2026-11-04T00:00:00Z", NOW);
    expect(future).toMatch(/The scheduled change to Lite will not happen\.$/);
  });
});

describe("a change the OpsGrids team scheduled", () => {
  it("is named as agreed with OpsGrids on the row", () => {
    expect(pendingLine(ref({ plan_name: "Growth (agreed)" }), "2026-11-04T00:00:00Z", { scheduledBy: "opsgrids" })).toBe(
      `Changes to Growth (agreed) on ${formatDate("2026-11-04T00:00:00Z")} · agreed with OpsGrids`,
    );
  });

  it("makes Change plan unavailable, with who to contact", () => {
    expect(changePlanUnavailableReason(sub({ state: "active" }), "opsgrids")).toBe(
      "A plan change agreed with OpsGrids is scheduled. Contact info@opsgrids.com to change it.",
    );
    expect(changePlanUnavailableReason(sub({ state: "active" }), "customer")).toBeNull();
  });

  it("is refused with a next step, not the server's internal sentence", () => {
    expect(refusalGuidance("scheduled_by_opsgrids")).toMatch(/Contact info@opsgrids\.com to change or cancel it/);
    expect(failureMessage("scheduled_by_opsgrids", "a plan change agreed with the OpsGrids team …", "x")).toBe(
      refusalGuidance("scheduled_by_opsgrids"),
    );
    expect(failureMessage("catalog_unavailable", "catalog did not answer which plans are on offer", "x")).not.toMatch(
      /catalog/,
    );
    expect(failureMessage(null, "the server's sentence", "x")).toBe("the server's sentence");
    expect(failureMessage(null, null, "fallback")).toBe("fallback");
  });
});

describe("a cancelled row", () => {
  it("never shows a scheduled change, whatever a cached summary says", () => {
    expect(pendingLine(ref({ plan_name: "Lite" }), "2026-11-04T00:00:00Z", { state: "cancelled" })).toBeNull();
  });
});

describe("a paid upgrade on a usage-priced plan", () => {
  it("notes the separate usage invoice at the old plan's rates", () => {
    const notes = previewNotes(preview({ usage_priced: true, usage_closed_now: true }), {
      fromPlan: "Growth",
      pendingPlan: null,
      productNames: (keys) => keys,
    });
    expect(notes).toContain(
      "Usage so far this period is invoiced separately at Growth's usage rates; usage from now on is billed at the new plan's.",
    );
  });

  it("says nothing extra about usage when no usage closes now", () => {
    const notes = previewNotes(preview({ usage_priced: false, usage_closed_now: false }), {
      fromPlan: "Growth",
      pendingPlan: null,
      productNames: (keys) => keys,
    });
    expect(notes.some((n) => /usage rates/.test(n))).toBe(false);
  });
});
