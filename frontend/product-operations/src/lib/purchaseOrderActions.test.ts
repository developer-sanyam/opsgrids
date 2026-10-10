import { describe, expect, it } from "vitest";

import { purchaseOrderActions } from "./purchaseOrderActions";

describe("purchaseOrderActions", () => {
  it("never offers Receive on a draft, approver or not", () => {
    expect(purchaseOrderActions("draft", false).receive).toBe(false);
    expect(purchaseOrderActions("draft", true).receive).toBe(false);
  });

  it("offers Receive once the order is approved, to the buyer as well", () => {
    expect(purchaseOrderActions("approved", false).receive).toBe(true);
    expect(purchaseOrderActions("approved", true).receive).toBe(true);
  });

  it("offers the goods-receipt flow on an approved or partially received order only", () => {
    expect(purchaseOrderActions("approved", false).receiveGoods).toBe(true);
    expect(purchaseOrderActions("partially_received", false).receiveGoods).toBe(true);
    expect(purchaseOrderActions("partially_received", false).cancel).toBe(false);
    for (const status of ["draft", "received", "closed", "cancelled"] as const) {
      expect(purchaseOrderActions(status, true).receiveGoods).toBe(false);
    }
  });

  it("offers Approve on a draft only to an approver", () => {
    expect(purchaseOrderActions("draft", false).approve).toBe(false);
    expect(purchaseOrderActions("draft", true).approve).toBe(true);
    expect(purchaseOrderActions("approved", true).approve).toBe(false);
  });

  it("lets a buyer cancel a draft but only an approver cancel an approved order", () => {
    expect(purchaseOrderActions("draft", false).cancel).toBe(true);
    expect(purchaseOrderActions("approved", false).cancel).toBe(false);
    expect(purchaseOrderActions("approved", true).cancel).toBe(true);
  });

  it("offers nothing but Close on a received order, and nothing at all once finished", () => {
    expect(purchaseOrderActions("received", true)).toEqual({
      edit: false,
      approve: false,
      receive: false,
      receiveGoods: false,
      cancel: false,
      close: true,
    });
    for (const status of ["closed", "cancelled"] as const) {
      expect(Object.values(purchaseOrderActions(status, true)).some(Boolean)).toBe(false);
    }
  });
});
