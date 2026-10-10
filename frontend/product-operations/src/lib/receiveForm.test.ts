import { describe, expect, it } from "vitest";

import type { TransferLineOut } from "@/api/movementTypes";
import { defaultReceiveValues, receivableLines, validateReceive } from "./receiveForm";

const NO_DISCRETE: ReadonlySet<string> = new Set();

function line(id: string, inTransit: string, overrides: Partial<TransferLineOut> = {}): TransferLineOut {
  return {
    id,
    line_no: 1,
    inventory_item_id: `item-${id}`,
    sku: `SKU-${id}`,
    item_name: "Steel coil",
    uom: "MT",
    from_location_id: "bin-src",
    from_location_code: "SRC-1",
    to_location_id: null,
    in_transit_location_id: null,
    lot_id: null,
    roll_id: null,
    quantity: "40",
    dispatched: "40",
    received: "0",
    shortage: "0",
    in_transit: inTransit,
    ...overrides,
  };
}

describe("receiving a transfer", () => {
  it("defaults to everything still in transit arriving in the default bin, skipping settled lines", () => {
    const lines = [line("a", "30.5"), line("b", "0")];
    expect(receivableLines(lines).map((l) => l.id)).toEqual(["a"]);
    expect(defaultReceiveValues(lines, "bin-default")).toEqual([
      { lineId: "a", received: "30.5", shortage: "0", toLocationId: "bin-default" },
    ]);
  });

  it("builds a full receipt with decimal strings", () => {
    const lines = [line("a", "30.5")];
    const result = validateReceive(lines, defaultReceiveValues(lines, "bin-default"), NO_DISCRETE, "");
    expect(result).toEqual({
      ok: true,
      payload: {
        lines: [{ line_id: "a", received_quantity: "30.5", shortage_quantity: "0", to_location_id: "bin-default" }],
      },
    });
  });

  it("supports a partial receipt: the line that is left blank is not sent", () => {
    const lines = [line("a", "30"), line("b", "10")];
    const result = validateReceive(
      lines,
      [
        { lineId: "a", received: "12.25", shortage: "0", toLocationId: "bin-default" },
        { lineId: "b", received: "0", shortage: "0", toLocationId: "bin-default" },
      ],
      NO_DISCRETE,
      "First pallet",
    );
    expect(result).toEqual({
      ok: true,
      payload: {
        lines: [{ line_id: "a", received_quantity: "12.25", shortage_quantity: "0", to_location_id: "bin-default" }],
        note: "First pallet",
      },
    });
  });

  it("declares a shortage alongside what arrived", () => {
    const lines = [line("a", "30")];
    const result = validateReceive(
      lines,
      [{ lineId: "a", received: "28", shortage: "2", toLocationId: "bin-default" }],
      NO_DISCRETE,
      "",
    );
    expect(result).toMatchObject({
      ok: true,
      payload: { lines: [{ received_quantity: "28", shortage_quantity: "2" }] },
    });
  });

  it("sends a shortage-only line without needing a bin", () => {
    const lines = [line("a", "5")];
    const result = validateReceive(lines, [{ lineId: "a", received: "0", shortage: "5", toLocationId: "" }], NO_DISCRETE, "");
    expect(result).toEqual({
      ok: true,
      payload: { lines: [{ line_id: "a", received_quantity: "0", shortage_quantity: "5" }] },
    });
  });

  it("refuses received plus short that exceeds what is in transit, exactly", () => {
    const lines = [line("a", "30")];
    const result = validateReceive(
      lines,
      [{ lineId: "a", received: "29.9", shortage: "0.101", toLocationId: "bin-default" }],
      NO_DISCRETE,
      "",
    );
    expect(result).toMatchObject({
      ok: false,
      errors: { a: { line: "Only 30 MT is still in transit — received plus short can't be more." } },
    });
  });

  it("accepts received plus short that lands exactly on what is in transit", () => {
    const lines = [line("a", "30")];
    const result = validateReceive(
      lines,
      [{ lineId: "a", received: "29.9", shortage: "0.1", toLocationId: "bin-default" }],
      NO_DISCRETE,
      "",
    );
    expect(result.ok).toBe(true);
  });

  it("refuses an invalid number, a negative, and a fraction of a discrete unit", () => {
    const lines = [line("a", "30", { uom: "pcs" })];
    const result = validateReceive(
      lines,
      [{ lineId: "a", received: "-1", shortage: "1.5", toLocationId: "bin-default" }],
      new Set(["pcs"]),
      "",
    );
    expect(result).toMatchObject({
      ok: false,
      errors: { a: { received: expect.any(String), shortage: "pcs is counted in whole units — no decimals." } },
    });
  });

  it("needs a bin for stock that arrives", () => {
    const lines = [line("a", "30")];
    const result = validateReceive(lines, [{ lineId: "a", received: "3", shortage: "0", toLocationId: "" }], NO_DISCRETE, "");
    expect(result).toMatchObject({ ok: false, errors: { a: { location: "Choose the bin the stock goes into." } } });
  });

  it("asks for something to be recorded when every line is empty", () => {
    const lines = [line("a", "30")];
    const result = validateReceive(lines, [{ lineId: "a", received: "0", shortage: "0", toLocationId: "x" }], NO_DISCRETE, "");
    expect(result).toEqual({
      ok: false,
      errors: {},
      formError: "Enter what arrived, or what is short, on at least one line.",
    });
  });
});
