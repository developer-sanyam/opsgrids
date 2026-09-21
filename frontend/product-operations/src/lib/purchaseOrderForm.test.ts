import { describe, expect, it } from "vitest";

import {
  buildPurchaseOrderPayload,
  emptyLine,
  emptyPurchaseOrder,
  hasErrors,
  lineAmountMinor,
  orderTotalMinor,
  parseQuantity,
  validatePurchaseOrder,
} from "./purchaseOrderForm";
import type { PurchaseOrderFormValues } from "./purchaseOrderForm";

/**
 * Component-level tests were not added for `PurchaseOrderForm.tsx`: this app carries no
 * `vitest.config.ts` entry for `@testing-library/react` / `jsdom` wiring, so the form's pure
 * logic — the part that actually has to be correct — is tested thoroughly here instead: quantity
 * and totals, validation for both create and edit, and the exact payload shape sent to the
 * server for each. Money parsing itself (`parseMinor`, including the line unit price's required
 * mode) lives in `@/lib/money.test.ts` — one parser, one test suite. The component wires these
 * functions to `SupplierPicker`/`SkuPicker`/`TextField` and is exercised by hand against
 * `VITE_USE_FIXTURES=true` instead.
 */

const TODAY = "2026-09-21";

function values(overrides: Partial<PurchaseOrderFormValues> = {}): PurchaseOrderFormValues {
  return { ...emptyPurchaseOrder("line-1"), ...overrides };
}

function validLine(overrides: Partial<ReturnType<typeof emptyLine>> = {}) {
  return { ...emptyLine("l1"), sku: "AL-6061-T6", quantity: "40", unitPrice: "2250", ...overrides };
}

describe("parseQuantity", () => {
  it("accepts a whole number greater than zero", () => {
    expect(parseQuantity("40")).toEqual({ ok: true, value: 40 });
    expect(parseQuantity("1")).toEqual({ ok: true, value: 1 });
  });

  it("refuses zero", () => {
    expect(parseQuantity("0").ok).toBe(false);
  });

  it("refuses a negative, fractional or empty quantity", () => {
    expect(parseQuantity("-4").ok).toBe(false);
    expect(parseQuantity("2.5").ok).toBe(false);
    expect(parseQuantity("").ok).toBe(false);
    expect(parseQuantity("forty").ok).toBe(false);
  });
});

describe("lineAmountMinor / orderTotalMinor", () => {
  it("computes a line's amount the same way the server does: quantity * unit_price_minor", () => {
    const line = { ...emptyLine("l1"), quantity: "40", unitPrice: "2250" };
    expect(lineAmountMinor(line)).toBe(40 * 225000);
  });

  it("is null for a half-typed row rather than a wrong number", () => {
    const line = { ...emptyLine("l1"), quantity: "40", unitPrice: "" };
    expect(lineAmountMinor(line)).toBeNull();
  });

  it("sums every valid line into the order total, skipping half-typed rows", () => {
    const lines = [
      { ...emptyLine("l1"), quantity: "2", unitPrice: "100" },
      { ...emptyLine("l2"), quantity: "3", unitPrice: "50" },
      { ...emptyLine("l3"), quantity: "", unitPrice: "" },
    ];
    expect(orderTotalMinor(lines)).toBe(2 * 10000 + 3 * 5000);
  });
});

describe("validatePurchaseOrder — create", () => {
  const create = { mode: "create" as const, today: TODAY };

  it("refuses a payload with no supplier chosen", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: null,
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
      create,
    );
    expect(found.supplier).toBe("Choose a supplier.");
    expect(hasErrors(found)).toBe(true);
  });

  it("requires a warehouse — PurchaseOrderIn.warehouse has min_length=1 on the wire", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: "sup-1",
        warehouse: "",
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
      create,
    );
    expect(found.warehouse).toBe("Choose a warehouse.");
  });

  it("requires an expected date, and refuses one in the past", () => {
    const missing = validatePurchaseOrder(
      values({ supplierId: "sup-1", warehouse: "Pune DC", expectedDate: "", lines: [validLine()] }),
      create,
    );
    expect(missing.expectedDate).toBe("Pick an expected delivery date.");

    const past = validatePurchaseOrder(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-01-01",
        lines: [validLine()],
      }),
      create,
    );
    expect(past.expectedDate).toBe("The expected date can't be in the past.");
  });

  it("accepts an expected date of today", () => {
    const found = validatePurchaseOrder(
      values({ supplierId: "sup-1", warehouse: "Pune DC", expectedDate: TODAY, lines: [validLine()] }),
      create,
    );
    expect(found.expectedDate).toBeUndefined();
  });

  it("refuses a line with a zero quantity", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine({ quantity: "0" })],
      }),
      create,
    );
    expect(found.lineErrors?.l1?.quantity).toBe("Quantity is a whole number greater than zero.");
    expect(hasErrors(found)).toBe(true);
  });

  it("requires at least one line item", () => {
    const found = validatePurchaseOrder(
      values({ supplierId: "sup-1", warehouse: "Pune DC", expectedDate: "2026-10-15", lines: [] }),
      create,
    );
    expect(found.lines).toBe("Add at least one line item.");
    expect(hasErrors(found)).toBe(true);
  });

  it("refuses more than the server's 200-line cap", () => {
    const lines = Array.from({ length: 201 }, (_, i) => validLine({ key: `l${i}`, sku: `SKU-${i}` }));
    const found = validatePurchaseOrder(
      values({ supplierId: "sup-1", warehouse: "Pune DC", expectedDate: "2026-10-15", lines }),
      create,
    );
    expect(found.lines).toBe("A purchase order can hold at most 200 lines.");
  });

  it("accepts exactly 200 lines", () => {
    const lines = Array.from({ length: 200 }, (_, i) => validLine({ key: `l${i}`, sku: `SKU-${i}` }));
    const found = validatePurchaseOrder(
      values({ supplierId: "sup-1", warehouse: "Pune DC", expectedDate: "2026-10-15", lines }),
      create,
    );
    expect(found.lines).toBeUndefined();
  });

  it("passes a fully valid form with no errors", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
      create,
    );
    expect(found).toEqual({});
    expect(hasErrors(found)).toBe(false);
  });

  it("does not require a line's description or uom — the server defaults both to \"\"", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine({ description: "", uom: "" })],
      }),
      create,
    );
    expect(hasErrors(found)).toBe(false);
  });
});

describe("validatePurchaseOrder — edit", () => {
  const edit = { mode: "edit" as const, today: TODAY };

  it("never requires a supplier — a draft's supplier can't be changed from this form", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: null,
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
      edit,
    );
    expect(found.supplier).toBeUndefined();
  });

  it("still requires a warehouse and an expected date", () => {
    const found = validatePurchaseOrder(
      values({ supplierId: null, warehouse: "", expectedDate: "", lines: [validLine()] }),
      edit,
    );
    expect(found.warehouse).toBe("Choose a warehouse.");
    expect(found.expectedDate).toBe("Pick an expected delivery date.");
  });

  it("allows an expected date in the past — PurchaseOrderPatch carries no such rule", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: null,
        warehouse: "Pune DC",
        expectedDate: "2020-01-01",
        lines: [validLine()],
      }),
      edit,
    );
    expect(found.expectedDate).toBeUndefined();
  });

  it("still validates every line", () => {
    const found = validatePurchaseOrder(
      values({
        supplierId: null,
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [validLine({ sku: "" })],
      }),
      edit,
    );
    expect(found.lineErrors?.l1?.sku).toBe("Enter a SKU.");
  });
});

describe("buildPurchaseOrderPayload — create", () => {
  it("sends exactly the fields PurchaseOrderIn accepts — no derived fields", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        supplierId: "3d3f0d8e-6b9a-4e9b-9c9a-5a1a5f2a2b7a",
        supplierName: "Ironclad Alloys",
        warehouse: "Pune DC",
        currency: "inr",
        expectedDate: "2026-10-15",
        notes: " Q4 raw materials ",
        lines: [
          {
            key: "l1",
            sku: "AL-6061-T6",
            description: "Aluminium billet",
            quantity: "40",
            unitPrice: "2250",
            uom: "kg",
          },
        ],
      }),
      { mode: "create", raisedBy: "Priya Nair" },
    );

    expect(payload).toEqual({
      supplier_id: "3d3f0d8e-6b9a-4e9b-9c9a-5a1a5f2a2b7a",
      warehouse: "Pune DC",
      expected_date: "2026-10-15",
      currency: "INR",
      notes: "Q4 raw materials",
      raised_by: "Priya Nair",
      lines: [
        {
          sku: "AL-6061-T6",
          description: "Aluminium billet",
          quantity: 40,
          unit_price_minor: 225000,
          uom: "kg",
        },
      ],
    });

    // The fields the server derives and refuses must never appear, at any level — PurchaseOrderIn
    // is `extra="forbid"`, so one of these would 422 the whole request rather than being ignored.
    expect(Object.keys(payload)).not.toContain("number");
    expect(Object.keys(payload)).not.toContain("status");
    expect(Object.keys(payload)).not.toContain("category");
    expect(Object.keys(payload)).not.toContain("raised_by_id");
    expect(Object.keys(payload)).not.toContain("total");
    expect(Object.keys(payload)).not.toContain("total_minor");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("amount_minor");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("id");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("received_qty");
  });

  it("omits notes, currency and raised_by entirely when blank rather than sending an empty string", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        currency: "  ",
        notes: "   ",
        lines: [{ key: "l1", sku: "SKU-1", description: "", quantity: "1", unitPrice: "10", uom: "" }],
      }),
      { mode: "create", raisedBy: "   " },
    );
    expect(payload.notes).toBeUndefined();
    expect(payload.currency).toBeUndefined();
    expect(payload.raised_by).toBeUndefined();
    expect(Object.keys(payload)).not.toContain("notes");
    expect(Object.keys(payload)).not.toContain("currency");
    expect(Object.keys(payload)).not.toContain("raised_by");
  });

  it("carries the unit price as integer minor units, matching the API contract's own example", () => {
    // ₹2,250.00 per unit is `unit_price_minor: 225000` in the published example payload.
    const payload = buildPurchaseOrderPayload(
      values({
        supplierId: "sup-1",
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [{ key: "l1", sku: "SKU-1", description: "", quantity: "1", unitPrice: "2250", uom: "" }],
      }),
      { mode: "create", raisedBy: "" },
    );
    expect(payload.lines[0]?.unit_price_minor).toBe(225000);
    expect(Number.isInteger(payload.lines[0]?.unit_price_minor)).toBe(true);
  });
});

describe("buildPurchaseOrderPayload — edit", () => {
  it("sends a PurchaseOrderPatch shape, never a supplier_id — the form has no control that changes it", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        supplierId: "sup-1",
        supplierName: "Ironclad Alloys",
        warehouse: "Chennai DC",
        currency: "usd",
        expectedDate: "2026-11-01",
        notes: "Rev 2",
        lines: [validLine()],
      }),
      { mode: "edit" },
    );

    expect(payload).toEqual({
      warehouse: "Chennai DC",
      expected_date: "2026-11-01",
      currency: "USD",
      notes: "Rev 2",
      lines: [
        {
          sku: "AL-6061-T6",
          description: "",
          quantity: 40,
          unit_price_minor: 225000,
          uom: "",
        },
      ],
    });
    expect(Object.keys(payload)).not.toContain("supplier_id");
    expect(Object.keys(payload)).not.toContain("raised_by");
    expect(Object.keys(payload)).not.toContain("number");
    expect(Object.keys(payload)).not.toContain("status");
  });

  it("always resends every line — PurchaseOrderPatch.lines is a full replacement, never a merge", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        warehouse: "Pune DC",
        expectedDate: "2026-10-15",
        lines: [
          validLine({ key: "l1", sku: "SKU-1" }),
          validLine({ key: "l2", sku: "SKU-2", quantity: "5" }),
        ],
      }),
      { mode: "edit" },
    );
    expect(payload.lines).toHaveLength(2);
    expect(payload.lines?.map((l) => l.sku)).toEqual(["SKU-1", "SKU-2"]);
  });

  it("omits currency and notes when blank, leaving the order's current values alone", () => {
    const payload = buildPurchaseOrderPayload(
      values({ warehouse: "Pune DC", expectedDate: "2026-10-15", currency: "", notes: "", lines: [validLine()] }),
      { mode: "edit" },
    );
    expect(Object.keys(payload)).not.toContain("currency");
    expect(Object.keys(payload)).not.toContain("notes");
  });
});
