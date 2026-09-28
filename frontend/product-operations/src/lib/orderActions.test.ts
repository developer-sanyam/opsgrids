import { describe, expect, it } from "vitest";

import { orderActions } from "./orderActions";

describe("orderActions", () => {
  it("offers Place only on a draft", () => {
    expect(orderActions("DRAFT", true, true).place).toBe(true);
    expect(orderActions("PLACED", true, true).place).toBe(false);
  });

  it("offers Accept only on a placed order, and only to a caller with the accept:ORG grant", () => {
    expect(orderActions("PLACED", true, true).accept).toBe(true);
    expect(orderActions("PLACED", false, true).accept).toBe(false);
    expect(orderActions("DRAFT", true, true).accept).toBe(false);
    expect(orderActions("ACCEPTED", true, true).accept).toBe(false);
  });

  it("offers Hold on an order actually in fulfilment, never on a draft or placed order", () => {
    for (const status of ["ACCEPTED", "IN_FULFILMENT", "READY"] as const) {
      expect(orderActions(status, true, true).hold).toBe(true);
    }
    expect(orderActions("DRAFT", true, true).hold).toBe(false);
    expect(orderActions("PLACED", true, true).hold).toBe(false);
    expect(orderActions("ON_HOLD", true, true).hold).toBe(false);
  });

  it("offers Release only once on hold", () => {
    expect(orderActions("ON_HOLD", true, true).release).toBe(true);
    expect(orderActions("ACCEPTED", true, true).release).toBe(false);
  });

  it("offers Cancel up through READY, and only to a caller with the cancel:ORG grant", () => {
    for (const status of ["DRAFT", "PLACED", "ACCEPTED", "IN_FULFILMENT", "ON_HOLD", "READY"] as const) {
      expect(orderActions(status, true, true).cancel).toBe(true);
      expect(orderActions(status, true, false).cancel).toBe(false);
    }
  });

  it("offers nothing at all once an order is finished, however it got there", () => {
    for (const status of ["HANDED_OVER", "DELIVERED", "COMPLETED", "CANCELLED", "REJECTED"] as const) {
      expect(Object.values(orderActions(status, true, true)).some(Boolean)).toBe(false);
    }
  });
});
