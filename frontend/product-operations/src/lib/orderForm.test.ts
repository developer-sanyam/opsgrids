import { describe, expect, it } from "vitest";

import {
  buildOrderPayload,
  emptyOrderForm,
  emptyOrderLine,
  hasOrderFormErrors,
  parseOrderQuantity,
  quotableOrderLines,
  validateOrderForm,
} from "./orderForm";
import type { OrderFormValues } from "./orderForm";

function values(overrides: Partial<OrderFormValues> = {}): OrderFormValues {
  return { ...emptyOrderForm("manual", "row-1"), ...overrides };
}

describe("parseOrderQuantity", () => {
  it("accepts a positive decimal with up to three places", () => {
    expect(parseOrderQuantity("5")).toEqual({ ok: true, value: "5" });
    expect(parseOrderQuantity(" 12.5 ")).toEqual({ ok: true, value: "12.5" });
    expect(parseOrderQuantity("0.125")).toEqual({ ok: true, value: "0.125" });
  });

  it("refuses zero, negative, more than three decimals, or non-numeric input", () => {
    expect(parseOrderQuantity("0").ok).toBe(false);
    expect(parseOrderQuantity("-1").ok).toBe(false);
    expect(parseOrderQuantity("1.2345").ok).toBe(false);
    expect(parseOrderQuantity("abc").ok).toBe(false);
    expect(parseOrderQuantity("").ok).toBe(false);
  });
});

describe("validateOrderForm", () => {
  it("requires a warehouse", () => {
    const found = validateOrderForm(values({ warehouseId: "" }));
    expect(found.warehouse).toBe("Choose a warehouse.");
  });

  it("requires a customer name or phone, but not both", () => {
    const neither = validateOrderForm(values({ warehouseId: "wh-1", customerName: "", customerPhone: "" }));
    expect(neither.customer).toBe("Enter the customer's name or phone number.");

    const nameOnly = validateOrderForm(
      values({
        warehouseId: "wh-1",
        customerName: "Asha Rao",
        customerPhone: "",
        lines: [{ ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "5" }],
      }),
    );
    expect(nameOnly.customer).toBeUndefined();

    const phoneOnly = validateOrderForm(
      values({
        warehouseId: "wh-1",
        customerName: "",
        customerPhone: "+91 98200 11122",
        lines: [{ ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "5" }],
      }),
    );
    expect(phoneOnly.customer).toBeUndefined();
  });

  it("requires at least one line, and flags an incomplete row", () => {
    const noLines = validateOrderForm(values({ warehouseId: "wh-1", customerName: "Asha", lines: [] }));
    expect(noLines.lines).toBe("Add at least one line item.");

    const incomplete = validateOrderForm(
      values({
        warehouseId: "wh-1",
        customerName: "Asha",
        lines: [emptyOrderLine("row-1")],
      }),
    );
    expect(incomplete.lineErrors?.["row-1"]?.item).toBe("Look up an item.");
    expect(incomplete.lineErrors?.["row-1"]?.quantity).toBeDefined();
  });

  it("accepts a fully filled line with no price override", () => {
    const found = validateOrderForm(
      values({
        warehouseId: "wh-1",
        customerName: "Asha",
        lines: [{ ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "5" }],
      }),
    );
    expect(hasOrderFormErrors(found)).toBe(false);
  });

  it("rejects a malformed price override", () => {
    const found = validateOrderForm(
      values({
        warehouseId: "wh-1",
        customerName: "Asha",
        lines: [
          { ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "5", priceOverride: "not-a-price" },
        ],
      }),
    );
    expect(found.lineErrors?.["row-1"]?.price).toBeDefined();
  });
});

describe("buildOrderPayload", () => {
  it("builds OrderIn with the mode's channel, trims the customer pair, and omits a blank price override", () => {
    const payload = buildOrderPayload(
      values({
        mode: "phone",
        warehouseId: "wh-1",
        customerName: "  Asha Rao  ",
        customerPhone: " +91 98200 11122 ",
        lines: [{ ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "5" }],
      }),
    );
    expect(payload).toEqual({
      channel: "phone",
      warehouse_id: "wh-1",
      customer_name: "Asha Rao",
      customer_phone: "+91 98200 11122",
      lines: [{ inventory_item_id: "item-1", quantity: "5" }],
    });
  });

  it("carries a typed price override as selling_price_minor, in paise", () => {
    const payload = buildOrderPayload(
      values({
        mode: "sales",
        warehouseId: "wh-1",
        customerName: "Asha",
        lines: [
          { ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "2", priceOverride: "250.50" },
        ],
      }),
    );
    expect(payload.lines).toEqual([{ inventory_item_id: "item-1", quantity: "2", selling_price_minor: 25050 }]);
    expect(payload.channel).toBe("sales");
  });

  it("never sends a blank customer field", () => {
    const payload = buildOrderPayload(
      values({
        warehouseId: "wh-1",
        customerName: "Asha",
        customerPhone: "",
        lines: [{ ...emptyOrderLine("row-1"), inventoryItemId: "item-1", quantity: "1" }],
      }),
    );
    expect(payload).not.toHaveProperty("customer_phone");
  });
});

describe("quotableOrderLines", () => {
  it("skips a line with no item picked or an unparseable quantity", () => {
    const lines = quotableOrderLines(
      values({
        lines: [
          emptyOrderLine("row-1"),
          { ...emptyOrderLine("row-2"), inventoryItemId: "item-2", quantity: "not-a-number" },
          { ...emptyOrderLine("row-3"), inventoryItemId: "item-3", quantity: "4" },
        ],
      }),
    );
    expect(lines).toEqual([{ inventory_item_id: "item-3", quantity: "4" }]);
  });
});
