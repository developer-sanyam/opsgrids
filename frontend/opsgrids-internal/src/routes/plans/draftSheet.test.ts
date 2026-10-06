import { describe, expect, it } from "vitest";

import type { PlanVersionOut } from "@/api/catalog";
import { buildVersionRequest, draftFromVersion, hasZeroAmount } from "./draftSheet";

function source(overrides: Partial<PlanVersionOut> = {}): PlanVersionOut {
  return {
    id: "v1",
    plan_id: "plan-1",
    plan_name: "Growth",
    product_key: "hrms",
    plan_state: "active",
    version_no: 1,
    state: "published",
    billing_interval: "month",
    currency: "INR",
    restricted_to_org_id: null,
    published_at: "2026-10-01T00:00:00Z",
    prices: [
      { id: "p-usage", kind: "usage", amount_minor: null, currency: "INR", unit: "email", unit_price_micros: 2500, meter_key: "emails_sent" },
      { id: "p-flat", kind: "flat", amount_minor: 149_900, currency: "INR", unit: null, unit_price_micros: null, meter_key: null },
      { id: "p-seat", kind: "per_seat", amount_minor: 19_900, currency: "INR", unit: "seat", unit_price_micros: null, meter_key: null },
    ],
    features: [
      { feature_key: "trial_days", value: 14 },
      { feature_key: "api_access", value: true },
    ],
    seat_classes: [
      { code: "member", name: "Member", price_id: "p-seat", included_count: null, can_write: true, sort_order: 10, description: null },
      { code: "viewer", name: "Viewer", price_id: null, included_count: null, can_write: false, sort_order: 20, description: null },
    ],
    ...overrides,
  };
}

describe("the editor always sends the whole sheet", () => {
  it("round-trips an unchanged version into a complete request, seat classes re-pointed by position", () => {
    const built = buildVersionRequest(draftFromVersion(source()), "Annual price review");
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request).toEqual({
      billing_interval: "month",
      currency: "INR",
      restricted_to_org_id: null,
      prices: [
        { kind: "usage", currency: "INR", unit: "email", unit_price_micros: 2500, meter_key: "emails_sent", amount_minor: null },
        { kind: "flat", currency: "INR", amount_minor: 149_900, unit: null, unit_price_micros: null, meter_key: null },
        { kind: "per_seat", currency: "INR", amount_minor: 19_900, unit: "seat", unit_price_micros: null, meter_key: null },
      ],
      features: [
        { feature_key: "api_access", value: true },
        { feature_key: "trial_days", value: 14 },
      ],
      seat_classes: [
        { code: "member", name: "Member", price_index: 2, included_count: null, can_write: true, sort_order: 10, description: null },
        { code: "viewer", name: "Viewer", price_index: null, included_count: null, can_write: false, sort_order: 20, description: null },
      ],
      reason: "Annual price review",
    });
  });

  it("follows a seat class's price when the rows above it are removed", () => {
    const draft = draftFromVersion(source());
    draft.prices = draft.prices.filter((p) => p.sourceId !== "p-usage");
    const built = buildVersionRequest(draft, "Drop metering");
    expect(built.ok && built.request.seat_classes[0]?.price_index).toBe(1);
  });

  it("refuses to drop the price a seat class is charged at", () => {
    const draft = draftFromVersion(source());
    draft.prices = draft.prices.filter((p) => p.sourceId !== "p-seat");
    const built = buildVersionRequest(draft, "Drop seats");
    expect(built.ok).toBe(false);
    if (built.ok) return;
    expect(built.errors["seatClasses.member"]).toMatch(/price you removed/);
  });

  it("keeps a bespoke audience and a bundle's members", () => {
    const draft = draftFromVersion(
      source({
        restricted_to_org_id: "0b6f3c1e-1d6e-4b8e-9b55-3f1c7c1a2d10",
        features: [{ feature_key: "bundled_products", value: ["hrms", "crm"] }],
        seat_classes: [],
      }),
    );
    const built = buildVersionRequest(draft, "Renewal for this client");
    expect(built.ok).toBe(true);
    if (!built.ok) return;
    expect(built.request.restricted_to_org_id).toBe("0b6f3c1e-1d6e-4b8e-9b55-3f1c7c1a2d10");
    expect(built.request.features).toContainEqual({ feature_key: "bundled_products", value: ["hrms", "crm"] });
  });

  it("names every invalid field at once", () => {
    const draft = draftFromVersion(null);
    draft.currency = "JPY";
    draft.prices = [
      { key: "a", sourceId: null, kind: "flat", amount: "12.5", unit: "", rate: "", meterKey: "" },
      { key: "b", sourceId: null, kind: "usage", amount: "", unit: "", rate: "0.5", meterKey: "" },
      { key: "c", sourceId: null, kind: "per_seat", amount: "100", unit: " ", rate: "", meterKey: "" },
    ];
    draft.trialDays = "400";
    draft.bundledProducts = "hrms, Not A Key";
    draft.restrictedToOrgId = "acme";
    const built = buildVersionRequest(draft, "x");
    expect(built.ok).toBe(false);
    if (built.ok) return;
    expect(Object.keys(built.errors).sort()).toEqual([
      "bundledProducts",
      "prices.0.amount",
      "prices.1.meterKey",
      "prices.2.unit",
      "reason",
      "restrictedToOrgId",
      "trialDays",
    ]);
  });

  it("flags a zero amount for publish's explicit acknowledgement", () => {
    expect(hasZeroAmount(source())).toBe(false);
    const free = source();
    free.prices = [{ id: "f", kind: "flat", amount_minor: 0, currency: "INR", unit: null, unit_price_micros: null, meter_key: null }];
    expect(hasZeroAmount(free)).toBe(true);
  });
});
