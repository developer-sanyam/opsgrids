import { afterEach, describe, expect, it, vi } from "vitest";

import { makeStockRow, makeSuggestion } from "@/worker/testKit";

import {
  barcodeProblem,
  buildPutawayBody,
  cleanBarcode,
  filterStock,
  hasAvailableStock,
  matchesSuggestion,
  queuedNotice,
  quantityProblem,
  stockDetail,
  utcDate,
  warningNotice,
} from "./putawayForm";

describe("cleanBarcode / barcodeProblem", () => {
  it("trims the CR/LF a keyboard-wedge scanner or a paste leaves on the ends", () => {
    expect(cleanBarcode("A-01-03\r\n")).toBe("A-01-03");
    expect(cleanBarcode("\nA-01-03\r")).toBe("A-01-03");
    expect(cleanBarcode("  A-01-03  ")).toBe("A-01-03");
  });

  it("accepts any printable-ASCII label the server could have stored", () => {
    expect(barcodeProblem("A-01-03")).toBeNull();
    expect(barcodeProblem("LOC.000123~x")).toBeNull();
    expect(barcodeProblem("x".repeat(64))).toBeNull();
  });

  it("refuses an empty label, and one the server's barcode shape could never contain", () => {
    expect(barcodeProblem("")).toMatch(/scan or type/i);
    expect(barcodeProblem("A 01")).toMatch(/bin label/i);
    expect(barcodeProblem("A/01")).toMatch(/bin label/i);
    expect(barcodeProblem("A\r\n01")).toMatch(/bin label/i);
    expect(barcodeProblem("é")).toMatch(/bin label/i);
    expect(barcodeProblem("x".repeat(65))).toMatch(/bin label/i);
  });
});

describe("quantityProblem", () => {
  it("accepts an exact decimal up to what is available", () => {
    expect(quantityProblem("5", "20", "m")).toBeNull();
    expect(quantityProblem("20", "20", "m")).toBeNull();
    expect(quantityProblem("0.001", "20", "m")).toBeNull();
    expect(quantityProblem("19.999", "20", "m")).toBeNull();
    expect(quantityProblem(" 7.5 ", "20", "m")).toBeNull();
  });

  it("compares in exact milli-units, never as floats", () => {
    expect(quantityProblem("0.3", "0.3", "m")).toBeNull();
    expect(quantityProblem("0.301", "0.3", "m")).toMatch(/available/i);
  });

  it.each(["", "  ", "abc", "1e3", "-1", "1,5", "1.2345", ".5", "5."])("refuses %j", (typed) => {
    expect(quantityProblem(typed, "20", "m")).not.toBeNull();
  });

  it("refuses zero and more than is there, naming the unit", () => {
    expect(quantityProblem("0", "20", "m")).toMatch(/more than zero/i);
    expect(quantityProblem("0.000", "20", "m")).toMatch(/more than zero/i);
    expect(quantityProblem("20.001", "20", "kg")).toBe("Only 20 kg is available there.");
  });
});

describe("stock helpers", () => {
  it("offers only stock that has something available", () => {
    expect(hasAvailableStock(makeStockRow({ available_qty: "3.5" }))).toBe(true);
    expect(hasAvailableStock(makeStockRow({ available_qty: "0" }))).toBe(false);
    expect(hasAvailableStock(makeStockRow({ available_qty: "0.000" }))).toBe(false);
  });

  const rows = [
    makeStockRow({ id: "a", sku: "SKU-100", item_name: "Cotton roll", location_code: "RCV-01", lot_no: "LOT-7" }),
    makeStockRow({ id: "b", sku: "SKU-200", item_name: "Denim", location_code: "STG-02", roll_no: "R-55" }),
  ];

  it("filters over SKU, item name, bin, warehouse, lot and roll — case-insensitively", () => {
    expect(filterStock(rows, "").map((r) => r.id)).toEqual(["a", "b"]);
    expect(filterStock(rows, "sku-2").map((r) => r.id)).toEqual(["b"]);
    expect(filterStock(rows, "COTTON").map((r) => r.id)).toEqual(["a"]);
    expect(filterStock(rows, "stg").map((r) => r.id)).toEqual(["b"]);
    expect(filterStock(rows, "lot-7").map((r) => r.id)).toEqual(["a"]);
    expect(filterStock(rows, "r-55").map((r) => r.id)).toEqual(["b"]);
    expect(filterStock(rows, "wh1").map((r) => r.id)).toEqual(["a", "b"]);
    expect(filterStock(rows, "nothing like this")).toEqual([]);
  });

  it("describes a bucket the way its shelf label reads", () => {
    expect(stockDetail(makeStockRow({ lot_no: "LOT-7", roll_no: "R-1", available_qty: "12500.5" }))).toBe(
      "In RCV-01 · WH1 · Lot LOT-7 · Roll R-1 · 12,500.5 m available",
    );
  });
});

describe("matchesSuggestion", () => {
  it("recognises a label that is the suggested bin's code, ignoring case", () => {
    expect(matchesSuggestion("A-01-03", makeSuggestion({ code: "A-01-03" }))).toBe(true);
    expect(matchesSuggestion("a-01-03", makeSuggestion({ code: "A-01-03" }))).toBe(true);
    expect(matchesSuggestion("B-02-01", makeSuggestion({ code: "A-01-03" }))).toBe(false);
  });
});

describe("utcDate", () => {
  const original = process.env.TZ;
  afterEach(() => {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  });

  it("is the UTC date, not the phone's, for a put-away just after UTC midnight in the Americas", () => {
    process.env.TZ = "America/Los_Angeles";
    vi.setSystemTime(new Date("2026-09-30T00:30:00.000Z"));

    // Local time is still the 29th, 5:30 pm — the date a local formatter would have used.
    expect(new Date().getDate()).toBe(29);
    expect(utcDate(new Date().toISOString())).toBe("2026-09-30");
  });

  it("is the UTC date just before UTC midnight in a timezone far ahead", () => {
    process.env.TZ = "Pacific/Auckland";
    vi.setSystemTime(new Date("2026-09-29T23:30:00.000Z"));

    expect(new Date().getDate()).toBe(30);
    expect(utcDate(new Date().toISOString())).toBe("2026-09-29");
  });
});

describe("buildPutawayBody", () => {
  const capturedAt = "2026-09-29T09:00:00.000Z";

  it("freezes the whole request: ids, quantity, label, times, suggestion and the GPS fix as integers", () => {
    const body = buildPutawayBody({
      row: makeStockRow({ lot_id: "lot-1", roll_id: "roll-1" }),
      quantity: " 5.0 ",
      toBarcode: "A-01-03",
      suggestion: makeSuggestion({ location_id: "loc-bin-1" }),
      capturedAt,
      geo: { lat_micro: 12_971_599, lng_micro: 77_594_563, accuracy_m: 12 },
    });

    expect(body).toEqual({
      inventory_item_id: "item-1",
      from_location_id: "loc-rcv-1",
      to_barcode: "A-01-03",
      quantity: "5",
      lot_id: "lot-1",
      roll_id: "roll-1",
      suggested_location_id: "loc-bin-1",
      captured_at: capturedAt,
      occurred_on: "2026-09-29",
      lat_micro: 12_971_599,
      lng_micro: 77_594_563,
      accuracy_m: 12,
    });
    for (const value of Object.values(body)) expect(typeof value === "number" ? Number.isInteger(value) : true).toBe(true);
  });

  it("leaves out what it doesn't have rather than sending nulls: no lot, roll, suggestion or fix", () => {
    const body = buildPutawayBody({
      row: makeStockRow(),
      quantity: "20",
      toBarcode: "A-01-03",
      suggestion: null,
      capturedAt,
      geo: null,
    });

    expect(Object.keys(body).sort()).toEqual(
      ["captured_at", "from_location_id", "inventory_item_id", "occurred_on", "quantity", "to_barcode"].sort(),
    );
  });

  it("a fix with no accuracy sends the pair alone", () => {
    const body = buildPutawayBody({
      row: makeStockRow(),
      quantity: "1",
      toBarcode: "A-01-03",
      suggestion: null,
      capturedAt,
      geo: { lat_micro: 1, lng_micro: 2 },
    });

    expect(body).toMatchObject({ lat_micro: 1, lng_micro: 2 });
    expect(body).not.toHaveProperty("accuracy_m");
  });
});

describe("notices", () => {
  it("has a sentence for every server warning, and none for one it doesn't know", () => {
    expect(warningNotice("geo_dropped:out_of_range")).toMatch(/location/i);
    expect(warningNotice("captured_at_adjusted")).toMatch(/clock/i);
    expect(warningNotice("geo_not_recorded")).toMatch(/location/i);
    expect(warningNotice("something_new")).toBeNull();
  });

  it("says why a put-away is waiting on the phone", () => {
    expect(queuedNotice("offline")).toMatch(/offline/i);
    expect(queuedNotice("network")).toMatch(/reach the server/i);
    expect(queuedNotice("server")).toMatch(/problem/i);
    expect(queuedNotice("session")).toMatch(/sign back in/i);
  });
});
