import { describe, expect, it } from "vitest";

import {
  buildPurchaseOrderPayload,
  emptyLine,
  emptyPurchaseOrder,
  hasErrors,
  lineAmountMinor,
  orderTotalMinor,
  parseQuantity,
  parseRupeesToMinor,
  validatePurchaseOrder,
} from "./purchaseOrderForm";
import type { PurchaseOrderFormValues } from "./purchaseOrderForm";

/**
 * Component-level tests were not added for `NewPurchaseOrderDialog.tsx`: this app carries no
 * `vitest.config.ts` and no `@testing-library/react` / `jsdom` dependency (unlike, say,
 * `product-hrms`, which has both). Adding that tooling was out of scope for the files this slice
 * owns, so the form's pure logic — the part that actually has to be correct — is tested
 * thoroughly here instead: money conversion, validation, and the exact payload shape sent to the
 * server. The component wires these functions to `Dialog`/`TextField`/`Combobox` and was
 * exercised by hand against `VITE_USE_FIXTURES=true` instead.
 */

function values(overrides: Partial<PurchaseOrderFormValues> = {}): PurchaseOrderFormValues {
  return { ...emptyPurchaseOrder("line-1"), ...overrides };
}

describe("parseRupeesToMinor", () => {
  it("reads whole rupees and paise without a float in the middle", () => {
    expect(parseRupeesToMinor("2250")).toEqual({ ok: true, minor: 225000 });
    expect(parseRupeesToMinor("2250.50")).toEqual({ ok: true, minor: 225050 });
    expect(parseRupeesToMinor("2250.5")).toEqual({ ok: true, minor: 225050 });
  });

  it("keeps a value with paise exact", () => {
    expect(parseRupeesToMinor("29.35")).toEqual({ ok: true, minor: 2935 });
    expect(parseRupeesToMinor("0.05")).toEqual({ ok: true, minor: 5 });
  });

  it("parses a value that would drift through a naive float multiply", () => {
    // `2250.99 * 100` is `225098.99999999997` in IEEE 754 — a `Math.round` papering over that
    // works until the amount is large enough that it doesn't. The digit-based parse never
    // constructs that float at all, so it lands on the exact integer every time.
    expect(2250.99 * 100).not.toBe(225099);
    expect(parseRupeesToMinor("2250.99")).toEqual({ ok: true, minor: 225099 });
  });

  it("tolerates what people paste", () => {
    expect(parseRupeesToMinor(" ₹1,499.00 ")).toEqual({ ok: true, minor: 149900 });
  });

  it("refuses an empty price — a line's unit price is required, never optional", () => {
    expect(parseRupeesToMinor("").ok).toBe(false);
    expect(parseRupeesToMinor("   ").ok).toBe(false);
  });

  it("refuses more than two decimal places instead of rounding them away", () => {
    expect(parseRupeesToMinor("29.999").ok).toBe(false);
  });

  it("refuses anything that is not an amount", () => {
    expect(parseRupeesToMinor("free").ok).toBe(false);
    expect(parseRupeesToMinor("-5").ok).toBe(false);
  });
});

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

describe("validatePurchaseOrder", () => {
  function validLine(overrides: Partial<ReturnType<typeof emptyLine>> = {}) {
    return { ...emptyLine("l1"), sku: "AL-6061-T6", quantity: "40", unitPrice: "2250", ...overrides };
  }

  it("refuses a payload with no supplier chosen", () => {
    const found = validatePurchaseOrder(
      values({
        number: "PO-2026-0147",
        supplierCode: null,
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
    );
    expect(found.supplier).toBe("Choose a supplier.");
    expect(hasErrors(found)).toBe(true);
  });

  it("refuses a line with a zero quantity", () => {
    const found = validatePurchaseOrder(
      values({
        number: "PO-2026-0147",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        lines: [validLine({ quantity: "0" })],
      }),
    );
    expect(found.lineErrors?.l1?.quantity).toBe("Quantity is a whole number greater than zero.");
    expect(hasErrors(found)).toBe(true);
  });

  it("requires a PO number and an expected date", () => {
    const found = validatePurchaseOrder(
      values({ number: "", supplierCode: "IRONCLAD", expectedDate: "", lines: [validLine()] }),
    );
    expect(found.number).toBe("Give the purchase order a number.");
    expect(found.expectedDate).toBe("Pick an expected delivery date.");
  });

  it("requires at least one line item", () => {
    const found = validatePurchaseOrder(
      values({
        number: "PO-2026-0147",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        lines: [],
      }),
    );
    expect(found.lines).toBe("Add at least one line item.");
    expect(hasErrors(found)).toBe(true);
  });

  it("passes a fully valid form with no errors", () => {
    const found = validatePurchaseOrder(
      values({
        number: "PO-2026-0147",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        lines: [validLine()],
      }),
    );
    expect(found).toEqual({});
    expect(hasErrors(found)).toBe(false);
  });

  it("does not require warehouse, description or uom — the server defaults every one to \"\"", () => {
    const found = validatePurchaseOrder(
      values({
        number: "PO-2026-0147",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        warehouse: "",
        lines: [validLine({ description: "", uom: "" })],
      }),
    );
    expect(hasErrors(found)).toBe(false);
  });
});

describe("buildPurchaseOrderPayload", () => {
  it("sends exactly the fields the server accepts — no derived fields", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        number: " PO-2026-0147 ",
        supplierCode: "IRONCLAD",
        supplierName: "Ironclad Alloys",
        status: "approved",
        warehouse: "Pune DC",
        currency: "INR",
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
    );

    expect(payload).toEqual({
      number: "PO-2026-0147",
      supplier_code: "IRONCLAD",
      status: "approved",
      warehouse: "Pune DC",
      currency: "INR",
      expected_date: "2026-10-15",
      notes: "Q4 raw materials",
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

    // The fields the server derives and refuses must never appear, at any level.
    expect(Object.keys(payload)).not.toContain("supplier_id");
    expect(Object.keys(payload)).not.toContain("category");
    expect(Object.keys(payload)).not.toContain("raised_by_id");
    expect(Object.keys(payload)).not.toContain("raised_by");
    expect(Object.keys(payload)).not.toContain("total");
    expect(Object.keys(payload)).not.toContain("total_minor");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("amount_minor");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("id");
    expect(Object.keys(payload.lines[0] ?? {})).not.toContain("received_qty");
  });

  it("omits notes entirely when blank rather than sending an empty string", () => {
    const payload = buildPurchaseOrderPayload(
      values({
        number: "PO-1",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        notes: "   ",
        lines: [{ key: "l1", sku: "SKU-1", description: "", quantity: "1", unitPrice: "10", uom: "" }],
      }),
    );
    expect(payload.notes).toBeUndefined();
    expect(Object.keys(payload)).not.toContain("notes");
  });

  it("carries the unit price as integer minor units, matching the API contract's own example", () => {
    // ₹2,250.00 per unit is `unit_price_minor: 225000` in the published example payload.
    const payload = buildPurchaseOrderPayload(
      values({
        number: "PO-1",
        supplierCode: "IRONCLAD",
        expectedDate: "2026-10-15",
        lines: [{ key: "l1", sku: "SKU-1", description: "", quantity: "1", unitPrice: "2250", uom: "" }],
      }),
    );
    expect(payload.lines[0]?.unit_price_minor).toBe(225000);
    expect(Number.isInteger(payload.lines[0]?.unit_price_minor)).toBe(true);
  });
});
