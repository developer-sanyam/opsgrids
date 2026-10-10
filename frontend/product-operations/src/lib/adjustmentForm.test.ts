import { describe, expect, it } from "vitest";

import { validateAdjustmentForm, validateThresholds } from "./adjustmentForm";
import type { AdjustmentFormValues } from "./adjustmentForm";

const NO_DISCRETE: ReadonlySet<string> = new Set();
const TODAY = "2026-10-05";
const UUID = "0b8f3a52-6f1d-4e0c-8a3e-3b7c5d9e1f20";

function form(overrides: Partial<AdjustmentFormValues> = {}): AdjustmentFormValues {
  return {
    inventoryItemId: "item-1",
    uom: "MT",
    locationId: "bin-1",
    delta: "-2.5",
    reason: "damaged",
    note: " water damage ",
    evidenceDocumentId: "",
    occurredOn: TODAY,
    lotId: "",
    rollId: "",
    ...overrides,
  };
}

describe("validateAdjustmentForm", () => {
  it("builds the payload with a signed decimal string", () => {
    expect(validateAdjustmentForm(form(), NO_DISCRETE, TODAY)).toEqual({
      ok: true,
      payload: {
        inventory_item_id: "item-1",
        location_id: "bin-1",
        quantity_delta: "-2.5",
        reason: "damaged",
        note: "water damage",
        occurred_on: TODAY,
      },
    });
  });

  it("sends the evidence document only when one is given, and checks its shape", () => {
    expect(validateAdjustmentForm(form({ evidenceDocumentId: UUID }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: true,
      payload: { evidence_document_id: UUID },
    });
    expect(validateAdjustmentForm(form({ evidenceDocumentId: "photo.jpg" }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: false,
      errors: { evidence: expect.any(String) },
    });
  });

  it("refuses a zero delta, a missing reason and a future date", () => {
    expect(validateAdjustmentForm(form({ delta: "0" }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: false,
      errors: { delta: "The quantity can't be zero." },
    });
    expect(validateAdjustmentForm(form({ reason: "" }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: false,
      errors: { reason: "Choose a reason." },
    });
    expect(validateAdjustmentForm(form({ occurredOn: "2026-10-06" }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: false,
      errors: { occurredOn: "The date can't be in the future." },
    });
  });

  it("accepts a positive delta and refuses a fraction of a discrete unit", () => {
    expect(validateAdjustmentForm(form({ delta: "4" }), NO_DISCRETE, TODAY)).toMatchObject({
      ok: true,
      payload: { quantity_delta: "4" },
    });
    expect(validateAdjustmentForm(form({ uom: "pcs", delta: "-1.5" }), new Set(["pcs"]), TODAY)).toMatchObject({
      ok: false,
      errors: { delta: "pcs is counted in whole units — no decimals." },
    });
  });
});

describe("validateThresholds", () => {
  it("sends both thresholds as decimal strings, a blank one as null", () => {
    expect(validateThresholds({ transfer: "500.50", adjustment: "" })).toEqual({
      ok: true,
      payload: { transfer_approval_threshold: "500.5", adjustment_approval_threshold: null },
    });
  });

  it("refuses zero, negatives and junk, naming the field", () => {
    expect(validateThresholds({ transfer: "0", adjustment: "-1" })).toMatchObject({
      ok: false,
      errors: { transfer: expect.any(String), adjustment: expect.any(String) },
    });
    expect(validateThresholds({ transfer: "abc", adjustment: "5" })).toMatchObject({
      ok: false,
      errors: { transfer: expect.any(String) },
    });
  });
});
