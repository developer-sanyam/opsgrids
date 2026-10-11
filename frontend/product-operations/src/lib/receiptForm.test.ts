import { describe, expect, it } from "vitest";

import type { ReceivingLineOut } from "@/api/types";
import {
  analyseLine,
  buildReceipt,
  emptyLineDraft,
  emptyRollDraft,
  enteredMilli,
  serverLineProblems,
  toStockMilli,
} from "./receiptForm";
import type { ReceiptLineDraft } from "./receiptForm";

function ctx(overrides: Partial<ReceivingLineOut> = {}): ReceivingLineOut {
  return {
    purchase_order_line_id: "line-1",
    sku: "FAB-100",
    description: "Cotton poplin",
    inventory_item_id: "item-1",
    stock_uom: "m",
    ordered_qty: "100",
    received_qty: "20",
    remaining_qty: "80",
    max_receivable_qty: "82",
    tolerance_pct: "2.5",
    short_closed: false,
    track_lot: false,
    track_roll: false,
    track_expiry: false,
    track_quality: false,
    default_location_id: "loc-1",
    default_location_label: "REC-1",
    conversions: [],
    ...overrides,
  };
}

function draft(overrides: Partial<ReceiptLineDraft> = {}): ReceiptLineDraft {
  return { ...emptyLineDraft("line-1"), ...overrides };
}

describe("enteredMilli", () => {
  it("reads a decimal string exactly, and refuses anything the server would", () => {
    expect(enteredMilli("82.5")).toBe(82500);
    expect(enteredMilli("0.001")).toBe(1);
    expect(enteredMilli("1.2345")).toBeNull();
    expect(enteredMilli("-1")).toBeNull();
    expect(enteredMilli("1e3")).toBeNull();
    expect(enteredMilli("")).toBeNull();
  });
});

describe("toStockMilli", () => {
  it("converts a container through its integer ratio without floats", () => {
    expect(toStockMilli(2000, { numerator: 12, denominator: 1 })).toEqual({ milli: 24000, exact: true });
  });

  it("flags a ratio that leaves a fraction of a milli-unit", () => {
    expect(toStockMilli(1000, { numerator: 1, denominator: 3 }).exact).toBe(false);
  });

  it("stays exact beyond the safe integer range", () => {
    const { milli, exact } = toStockMilli(1_000_000_000_000, { numerator: 1_000_000_000, denominator: 1_000_000_000 });
    expect(milli).toBe(1_000_000_000_000);
    expect(exact).toBe(true);
  });
});

describe("analyseLine", () => {
  it("leaves a line with no quantity out of the receipt, silently", () => {
    const a = analyseLine(draft(), ctx());
    expect(a.included).toBe(false);
    expect(a.errors.quantity).toBeUndefined();
  });

  it("rejects a quantity that is not a decimal string with at most three decimals", () => {
    expect(analyseLine(draft({ quantity: "1.2345" }), ctx()).errors.quantity).toMatch(/at most three decimals/);
    expect(analyseLine(draft({ quantity: "abc" }), ctx()).errors.quantity).toMatch(/at most three decimals/);
  });

  it("asks for Close short when the quantity is zero", () => {
    expect(analyseLine(draft({ quantity: "0" }), ctx()).errors.quantity).toMatch(/Close short/);
    expect(analyseLine(draft({ quantity: "0", closeShort: true }), ctx()).errors.quantity).toBeUndefined();
  });

  it("hints when the receipt leaves part of the line still expected", () => {
    const a = analyseLine(draft({ quantity: "50" }), ctx());
    expect(a.errors.quantity).toBeUndefined();
    expect(a.hints.join(" ")).toMatch(/30 m would still be expected/);
  });

  it("warns, without blocking, above what remains but within tolerance", () => {
    const a = analyseLine(draft({ quantity: "81" }), ctx());
    expect(a.errors.quantity).toBeUndefined();
    expect(a.hints.join(" ")).toMatch(/1 m over what the order still expects, within the 2.5% tolerance/);
  });

  it("blocks above the most that may be received", () => {
    const a = analyseLine(draft({ quantity: "82.001" }), ctx());
    expect(a.errors.quantity).toMatch(/at most 82 m/);
  });

  it("says what closing short gives up", () => {
    const a = analyseLine(draft({ quantity: "60", closeShort: true }), ctx());
    expect(a.hints.join(" ")).toMatch(/20 m will no longer be expected/);
  });

  it("converts a container before comparing with the ceiling", () => {
    const withBox = ctx({ stock_uom: "pcs", conversions: [{ uom: "box", numerator: 12, denominator: 1 }] });
    // 7 boxes = 84 pcs, over the 82 ceiling.
    expect(analyseLine(draft({ quantity: "7", uom: "box" }), withBox).errors.quantity).toMatch(/at most 82 pcs/);
    expect(analyseLine(draft({ quantity: "6", uom: "box" }), withBox).stockMilli).toBe(72000);
  });

  it("requires an expiry date when the item expires, and not otherwise", () => {
    const expiring = ctx({ track_lot: true, track_expiry: true });
    expect(analyseLine(draft({ quantity: "10" }), expiring).errors.expiresOn).toMatch(/expiry/);
    expect(analyseLine(draft({ quantity: "10", expiresOn: "2027-01-01" }), expiring).errors.expiresOn).toBeUndefined();
    expect(analyseLine(draft({ quantity: "10" }), ctx({ track_lot: true })).errors.expiresOn).toBeUndefined();
  });

  describe("rolls", () => {
    const rolled = ctx({ track_roll: true });
    const roll = (key: string, primary: string) => ({ ...emptyRollDraft(key), primary });

    it("needs at least one roll", () => {
      expect(analyseLine(draft({ quantity: "50" }), rolled).errors.rolls).toMatch(/Add the rolls/);
    });

    it("blocks rolls that do not add up exactly to the line quantity", () => {
      const a = analyseLine(draft({ quantity: "50", rolls: [roll("a", "30"), roll("b", "19.999")] }), rolled);
      expect(a.rollsMilli).toBe(49999);
      expect(a.errors.rolls).toMatch(/add up to 49.999 m, but this line receives 50 m/);
    });

    it("accepts rolls that add up exactly, with no float drift", () => {
      const a = analyseLine(draft({ quantity: "0.3", rolls: [roll("a", "0.1"), roll("b", "0.2")] }), rolled);
      expect(a.errors.rolls).toBeUndefined();
    });

    it("needs a measured weight and its unit together", () => {
      const a = analyseLine(
        draft({ quantity: "10", rolls: [{ ...roll("a", "10"), secondary: "1.5" }] }),
        rolled,
      );
      expect(a.errors.roll.a?.secondaryUom).toMatch(/unit/);
    });

    it("refuses a repeated roll number", () => {
      const a = analyseLine(
        draft({ quantity: "20", rolls: [{ ...roll("a", "10"), rollNo: "R1" }, { ...roll("b", "10"), rollNo: "R1" }] }),
        rolled,
      );
      expect(a.errors.roll.b?.rollNo).toMatch(/share/);
    });
  });
});

describe("buildReceipt", () => {
  const header = { receivedOn: "2026-10-05", deliveryNote: " DN-7 ", note: "" };

  it("says so when no line has a quantity", () => {
    const built = buildReceipt("po-1", header, [draft()], [ctx()]);
    expect(built.payload).toBeNull();
    expect(built.formError).toMatch(/at least one line/);
  });

  it("builds decimal-string quantities and omits what was not entered", () => {
    const built = buildReceipt("po-1", header, [draft({ quantity: "50.5" })], [ctx()]);
    expect(built.payload).toEqual({
      purchase_order_id: "po-1",
      received_on: "2026-10-05",
      delivery_note: "DN-7",
      lines: [{ purchase_order_line_id: "line-1", quantity: "50.5" }],
    });
    expect(typeof built.payload?.lines[0]?.quantity).toBe("string");
  });

  it("forces hold on for an item that tracks quality, and sends it only when held", () => {
    const quality = buildReceipt("po-1", header, [draft({ quantity: "5" })], [ctx({ track_quality: true })]);
    expect(quality.payload?.lines[0]?.hold).toBe(true);
    const plain = buildReceipt("po-1", header, [draft({ quantity: "5" })], [ctx()]);
    expect(plain.payload?.lines[0]).not.toHaveProperty("hold");
    const asked = buildReceipt("po-1", header, [draft({ quantity: "5", hold: true })], [ctx()]);
    expect(asked.payload?.lines[0]?.hold).toBe(true);
  });

  it("sends the entered container unit, a new lot, and the rolls", () => {
    const built = buildReceipt(
      "po-1",
      header,
      [
        draft({
          quantity: "3",
          uom: "box",
          lotNo: "L-9",
          expiresOn: "2027-02-01",
          closeShort: true,
          locationId: "loc-9",
        }),
      ],
      [ctx({ stock_uom: "pcs", track_lot: true, track_expiry: true, conversions: [{ uom: "box", numerator: 12, denominator: 1 }] })],
    );
    expect(built.payload?.lines[0]).toEqual({
      purchase_order_line_id: "line-1",
      quantity: "3",
      uom: "box",
      location_id: "loc-9",
      close_short: true,
      lot: { lot_no: "L-9", expires_on: "2027-02-01" },
    });
  });

  it("sends nothing but the quantity and close_short for a line that receives zero", () => {
    const built = buildReceipt(
      "po-1",
      header,
      [draft({ quantity: "0", closeShort: true, hold: true, lotNo: "X" })],
      [ctx({ track_lot: true })],
    );
    expect(built.payload?.lines[0]).toEqual({ purchase_order_line_id: "line-1", quantity: "0", close_short: true });
  });

  it("builds no payload while any included line has an error", () => {
    const built = buildReceipt("po-1", header, [draft({ quantity: "999" })], [ctx()]);
    expect(built.payload).toBeNull();
    expect(built.formError).toBeNull();
    expect(built.analyses["line-1"]?.errors.quantity).toBeDefined();
  });

  it("sends rolls with a paired weight and unit", () => {
    const built = buildReceipt(
      "po-1",
      header,
      [
        draft({
          quantity: "50.5",
          rolls: [{ ...emptyRollDraft("a"), primary: "50.5", secondary: "14.8", secondaryUom: "kg", widthMm: "1500", gsm: "180" }],
        }),
      ],
      [ctx({ track_roll: true })],
    );
    expect(built.payload?.lines[0]?.rolls).toEqual([
      { primary_qty: "50.5", secondary_qty: "14.8", secondary_uom: "kg", width_mm: 1500, gsm: 180 },
    ]);
  });
});

describe("serverLineProblems", () => {
  it("maps a request line index back to its purchase order line", () => {
    const { byLine, header } = serverLineProblems(
      {
        "lines.1.quantity": "Over the receivable quantity",
        "lines.1.rolls": "Rolls do not add up to the line quantity",
        "lines[0].location_id": "Not in this warehouse",
        received_on: "Cannot be in the future",
      },
      ["line-a", "line-b"],
    );
    expect(byLine["line-b"]).toEqual({ quantity: "Over the receivable quantity", other: ["Rolls do not add up to the line quantity"] });
    expect(byLine["line-a"]).toEqual({ other: ["Not in this warehouse"] });
    expect(header).toEqual({ received_on: "Cannot be in the future" });
  });
});
