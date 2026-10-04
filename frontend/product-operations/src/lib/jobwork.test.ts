import { describe, expect, it } from "vitest";

import type { JobWorkMaterialOut, JobWorkReconciliationOut } from "@/api/types";
import {
  closeBlockReason,
  newIdempotencyKey,
  orderActions,
  outstandingAfter,
  parseQtyInput,
  reconciliationFoots,
  validateReceipt,
} from "./jobwork";

function material(overrides: Partial<JobWorkMaterialOut> = {}): JobWorkMaterialOut {
  return {
    id: "m1",
    line_no: 1,
    item_id: "i1",
    sku: "FAB-GREIGE",
    uom: "m",
    lot_id: null,
    roll_id: null,
    location_id: "loc-1",
    quantity: "82.5",
    sent: "82.5",
    received: "0",
    rejected: "0",
    shortage: "0",
    outstanding: "82.5",
    unit_value_minor: 40000,
    currency: "INR",
    ...overrides,
  };
}

function reconciliation(overrides: Partial<JobWorkReconciliationOut> = {}): JobWorkReconciliationOut {
  return {
    order_id: "o1",
    order_status: "ISSUED",
    issued: true,
    lines: [],
    total_sent: "82.5",
    total_received: "30.125",
    total_rejected: "2.375",
    total_shortage: "0",
    total_outstanding: "50",
    documents_settled: false,
    ledger_consistent: true,
    drift: [],
    balanced: false,
    ...overrides,
  };
}

describe("parseQtyInput", () => {
  it("accepts exact decimals and returns the canonical string", () => {
    expect(parseQtyInput("82.5")).toEqual({ ok: true, milli: 82500, qty: "82.5" });
    expect(parseQtyInput("0.125")).toEqual({ ok: true, milli: 125, qty: "0.125" });
    expect(parseQtyInput(" 3.50 ")).toEqual({ ok: true, milli: 3500, qty: "3.5" });
    expect(parseQtyInput("")).toEqual({ ok: true, milli: 0, qty: "0" });
  });

  it("refuses what the server would refuse, without rounding", () => {
    for (const bad of ["1.0005", "-1", "1e3", "abc", "1,5", ".5", "5."]) {
      expect(parseQtyInput(bad).ok, bad).toBe(false);
    }
  });

  it("refuses a fraction of a discrete unit", () => {
    expect(parseQtyInput("2.5", "pcs").ok).toBe(false);
    expect(parseQtyInput("2", "pcs").ok).toBe(true);
    expect(parseQtyInput("2.5", "m").ok).toBe(true);
  });

  it("never loses a milli-unit that a float would", () => {
    // 0.1 + 0.2 !== 0.3 in floats; in milli-units the sum is exact.
    const a = parseQtyInput("0.1");
    const b = parseQtyInput("0.2");
    expect(a.ok && b.ok && a.milli + b.milli).toBe(300);
  });
});

describe("validateReceipt", () => {
  const draft = (received: string, rejected = "", shortage = "") => ({
    m1: { received, rejected, shortage },
  });

  it("builds exact decimal-string lines and skips untouched rows", () => {
    const result = validateReceipt([material(), material({ id: "m2", sku: "B" })], {
      ...draft("30.125", "2.375"),
      m2: { received: "", rejected: "", shortage: "" },
    });
    expect(result.errors).toEqual({});
    expect(result.lines).toEqual([
      { material_id: "m1", received: "30.125", rejected: "2.375", shortage: "0" },
    ]);
  });

  it("passes the chosen location through", () => {
    const result = validateReceipt([material()], draft("1"), () => "loc-9");
    expect(result.lines[0]?.location_id).toBe("loc-9");
  });

  it("accepts exactly the outstanding quantity and refuses one milli-unit more", () => {
    expect(validateReceipt([material()], draft("82.5")).lines).toHaveLength(1);
    const over = validateReceipt([material()], draft("82.5", "0.001"));
    expect(over.lines).toEqual([]);
    expect(over.errors.m1).toMatch(/more than the 82.5 m still at the vendor/);
  });

  it("counts received, rejected and shortage together against what is outstanding", () => {
    const over = validateReceipt([material({ outstanding: "10" })], draft("4", "4", "2.001"));
    expect(over.errors.m1).toBeDefined();
    expect(validateReceipt([material({ outstanding: "10" })], draft("4", "4", "2")).errors).toEqual({});
  });

  it("flags an unparseable cell on its own row and sends nothing", () => {
    const result = validateReceipt([material()], draft("1.0005"));
    expect(result.errors.m1).toMatch(/three decimals/);
    expect(result.lines).toEqual([]);
  });

  it("asks for something when nothing was entered", () => {
    const result = validateReceipt([material()], draft(""));
    expect(result.formError).toMatch(/at least one material/);
    expect(result.lines).toEqual([]);
  });
});

describe("outstandingAfter", () => {
  it("shows what would remain, exactly, and goes negative when over-entered", () => {
    const m = material();
    expect(outstandingAfter(m, undefined)).toBe("82.5");
    expect(outstandingAfter(m, { received: "30.125", rejected: "2.375", shortage: "" })).toBe("50");
    expect(outstandingAfter(m, { received: "82.501", rejected: "", shortage: "" })).toBe("-0.001");
    expect(outstandingAfter(m, { received: "abc", rejected: "", shortage: "" })).toBe("82.5");
  });
});

describe("orderActions", () => {
  it("follows the state machine", () => {
    expect(orderActions("DRAFT")).toEqual({
      approve: true,
      cancel: true,
      issue: false,
      receive: false,
      close: false,
    });
    expect(orderActions("APPROVED")).toMatchObject({ issue: true, cancel: true, approve: false });
    expect(orderActions("ISSUED")).toMatchObject({ receive: true, close: true, cancel: false });
    expect(orderActions("PARTIALLY_RECEIVED")).toMatchObject({ receive: true, close: true });
    expect(orderActions("RECEIVED")).toMatchObject({ receive: false, close: true });
    for (const done of ["CLOSED", "CANCELLED"] as const) {
      expect(Object.values(orderActions(done)).some(Boolean)).toBe(false);
    }
  });
});

describe("reconciliation helpers", () => {
  it("explains why a close would be refused", () => {
    expect(closeBlockReason(reconciliation())).toMatch(/50 is still unaccounted/);
    expect(closeBlockReason(reconciliation({ issued: false }))).toMatch(/Nothing has been issued/);
    expect(
      closeBlockReason(reconciliation({ documents_settled: true, ledger_consistent: false })),
    ).toMatch(/disagree/);
    expect(
      closeBlockReason(reconciliation({ documents_settled: true, total_outstanding: "0" })),
    ).toBeNull();
  });

  it("checks that the figures foot on the exact strings", () => {
    expect(reconciliationFoots(reconciliation())).toBe(true);
    expect(reconciliationFoots(reconciliation({ total_outstanding: "49.999" }))).toBe(false);
  });
});

describe("newIdempotencyKey", () => {
  it("is long enough for the server and different each time", () => {
    const a = newIdempotencyKey();
    expect(a.length).toBeGreaterThanOrEqual(8);
    expect(newIdempotencyKey()).not.toBe(a);
  });
});
