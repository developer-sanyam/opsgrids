import { describe, expect, it } from "vitest";

import { blankTransferLine, validateTransferForm } from "./transferForm";
import type { TransferFormValues, TransferLineValues } from "./transferForm";

const NO_DISCRETE: ReadonlySet<string> = new Set();
const UUID = "3f2b8c1e-4a9d-4e0a-9d51-2f7a1c0b8e11";

function line(overrides: Partial<TransferLineValues> = {}): TransferLineValues {
  return {
    ...blankTransferLine("k1"),
    inventoryItemId: "item-1",
    uom: "MT",
    fromLocationId: "bin-1",
    quantity: "82.5",
    ...overrides,
  };
}

function form(overrides: Partial<TransferFormValues> = {}): TransferFormValues {
  return {
    fromWarehouseId: "wh-1",
    toWarehouseId: "wh-2",
    lines: [line()],
    note: " Urgent ",
    expectedOn: "2026-10-20",
    ...overrides,
  };
}

describe("validateTransferForm", () => {
  it("builds the payload with the quantity as the decimal string that was typed", () => {
    const result = validateTransferForm(form(), NO_DISCRETE);
    expect(result).toEqual({
      ok: true,
      payload: {
        from_warehouse_id: "wh-1",
        to_warehouse_id: "wh-2",
        lines: [{ inventory_item_id: "item-1", from_location_id: "bin-1", quantity: "82.5" }],
        note: "Urgent",
        expected_on: "2026-10-20",
      },
    });
  });

  it("never sends a number for a quantity", () => {
    const result = validateTransferForm(form({ lines: [line({ quantity: "3.250" })] }), NO_DISCRETE);
    if (!result.ok) throw new Error("expected a payload");
    for (const sent of result.payload.lines) expect(typeof sent.quantity).toBe("string");
    expect(result.payload.lines[0]?.quantity).toBe("3.25");
  });

  it("sends lot and roll ids only when they are filled in", () => {
    const result = validateTransferForm(form({ lines: [line({ lotId: UUID, rollId: "" })] }), NO_DISCRETE);
    if (!result.ok) throw new Error("expected a payload");
    expect(result.payload.lines[0]).toMatchObject({ lot_id: UUID });
    expect(result.payload.lines[0]).not.toHaveProperty("roll_id");
  });

  it("omits the expected date when none is chosen", () => {
    const result = validateTransferForm(form({ expectedOn: "" }), NO_DISCRETE);
    if (!result.ok) throw new Error("expected a payload");
    expect(result.payload).not.toHaveProperty("expected_on");
  });

  it("refuses the same warehouse at both ends, and missing warehouses", () => {
    expect(validateTransferForm(form({ toWarehouseId: "wh-1" }), NO_DISCRETE)).toMatchObject({
      ok: false,
      errors: { toWarehouse: "The destination must differ from the source warehouse." },
    });
    expect(validateTransferForm(form({ fromWarehouseId: "", toWarehouseId: "" }), NO_DISCRETE)).toMatchObject({
      ok: false,
      errors: { fromWarehouse: expect.any(String), toWarehouse: expect.any(String) },
    });
  });

  it("refuses a transfer with no lines", () => {
    expect(validateTransferForm(form({ lines: [] }), NO_DISCRETE)).toMatchObject({
      ok: false,
      errors: { lines: "Add at least one line." },
    });
  });

  it("puts each line's problems on its own control", () => {
    const result = validateTransferForm(
      form({ lines: [line({ inventoryItemId: "", fromLocationId: "", quantity: "0", lotId: "nope" })] }),
      NO_DISCRETE,
    );
    expect(result).toMatchObject({
      ok: false,
      errors: {
        lineErrors: {
          k1: {
            item: "Choose an item.",
            location: "Choose the bin it is taken from.",
            quantity: "The quantity can't be zero.",
            lot: expect.any(String),
          },
        },
      },
    });
  });

  it("refuses more than three decimals and a fraction of a discrete unit", () => {
    expect(validateTransferForm(form({ lines: [line({ quantity: "1.2345" })] }), NO_DISCRETE)).toMatchObject({
      ok: false,
      errors: { lineErrors: { k1: { quantity: expect.stringContaining("3 decimal places") } } },
    });
    expect(
      validateTransferForm(form({ lines: [line({ uom: "pcs", quantity: "2.5" })] }), new Set(["pcs"])),
    ).toMatchObject({
      ok: false,
      errors: { lineErrors: { k1: { quantity: "pcs is counted in whole units — no decimals." } } },
    });
  });

  it("asks for the lines to be combined when one bucket appears twice", () => {
    const result = validateTransferForm(form({ lines: [line(), line({ key: "k2" })] }), NO_DISCRETE);
    expect(result).toMatchObject({
      ok: false,
      errors: { lineErrors: { k2: { line: expect.stringContaining("Combine the lines") } } },
    });
  });
});
