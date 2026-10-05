import { afterEach, describe, expect, it, vi } from "vitest";

import type { PutawayIn } from "@/api/types";
import type { PutawayQueueEntry } from "@/lib/putawayQueue";
import { makeStockRow, makeSuggestion } from "@/worker/testKit";

import {
  barcodeProblem,
  buildPutawayBody,
  cleanBarcode,
  deductQueued,
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
  it("recognises a label that is the suggested bin's barcode, ignoring case", () => {
    const suggestion = makeSuggestion({ code: "A-01-03", barcode: "LBL-A0103" });
    expect(matchesSuggestion("LBL-A0103", suggestion)).toBe(true);
    expect(matchesSuggestion("lbl-a0103", suggestion)).toBe(true);
    expect(matchesSuggestion("Lbl-A0103", suggestion)).toBe(true);
    expect(matchesSuggestion("LBL-A0104", suggestion)).toBe(false);
  });

  it("a code that is not the barcode is not a label: the server resolves a scan by barcode alone", () => {
    const suggestion = makeSuggestion({ code: "A-01-03", barcode: "LBL-A0103" });
    expect(matchesSuggestion("A-01-03", suggestion)).toBe(false);
    expect(matchesSuggestion("a-01-03", suggestion)).toBe(false);
  });

  it("a bin with no barcode cannot be matched by anything a worker could scan, its code included", () => {
    const suggestion = makeSuggestion({ code: "A-01-03", barcode: null });
    expect(matchesSuggestion("A-01-03", suggestion)).toBe(false);
    expect(matchesSuggestion("a-01-03", suggestion)).toBe(false);
    expect(matchesSuggestion("null", suggestion)).toBe(false);
    expect(matchesSuggestion("", suggestion)).toBe(false);
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
      online: false,
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
      online: false,
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
      online: false,
      geo: { lat_micro: 1, lng_micro: 2 },
    });

    expect(body).toMatchObject({ lat_micro: 1, lng_micro: 2 });
    expect(body).not.toHaveProperty("accuracy_m");
  });

  it("confirmed online it leaves occurred_on out, so the server's own date applies and a wrong phone clock cannot send a date it refuses", () => {
    // A phone whose clock is a day ahead: its UTC date is tomorrow as far as the server is concerned.
    const body = buildPutawayBody({
      row: makeStockRow(),
      quantity: "1",
      toBarcode: "A-01-03",
      suggestion: null,
      capturedAt: "2026-09-30T09:00:00.000Z",
      online: true,
      geo: null,
    });

    expect(body).not.toHaveProperty("occurred_on");
    expect(body.captured_at).toBe("2026-09-30T09:00:00.000Z");
  });

  it("confirmed offline it freezes the phone's UTC date, so a replay days later still names the day the stock moved", () => {
    const body = buildPutawayBody({
      row: makeStockRow(),
      quantity: "1",
      toBarcode: "A-01-03",
      suggestion: null,
      capturedAt,
      online: false,
      geo: null,
    });

    expect(body.occurred_on).toBe("2026-09-29");
  });
});

describe("deductQueued", () => {
  function queued(body: Partial<PutawayIn>, status: PutawayQueueEntry["status"] = "pending"): PutawayQueueEntry {
    return {
      ownerSub: "user-1",
      organizationId: "org-1",
      idempotencyKey: `key-${Math.random()}`,
      body: {
        inventory_item_id: "item-1",
        from_location_id: "loc-rcv-1",
        to_barcode: "A-01-03",
        quantity: "5",
        ...body,
      },
      summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
      queuedAt: "2026-09-29T09:00:00.000Z",
      status,
      attention: status === "needs_attention" ? { httpStatus: 409, message: "no" } : null,
    };
  }

  it("takes what is waiting to sync out of the bucket it came from, so the same stock is not offered twice", () => {
    const rows = [makeStockRow({ available_qty: "20" })];

    const [row] = deductQueued(rows, [queued({ quantity: "5" })]);

    expect(row?.available_qty).toBe("15");
  });

  it("adds up several waiting entries, exactly, in milli-units", () => {
    const rows = [makeStockRow({ available_qty: "1" })];

    const [row] = deductQueued(rows, [queued({ quantity: "0.1" }), queued({ quantity: "0.2" })]);

    expect(row?.available_qty).toBe("0.7");
  });

  it("drops a bucket once everything in it is spoken for", () => {
    const rows = [makeStockRow({ id: "a", available_qty: "5" }), makeStockRow({ id: "b", inventory_item_id: "item-2" })];

    expect(deductQueued(rows, [queued({ quantity: "5" })]).map((row) => row.id)).toEqual(["b"]);
  });

  it("only touches the bucket it matches: same item and bin, and the same lot and roll", () => {
    const rows = [
      makeStockRow({ id: "plain" }),
      makeStockRow({ id: "lot", lot_id: "lot-1" }),
      makeStockRow({ id: "roll", roll_id: "roll-1" }),
      makeStockRow({ id: "elsewhere", location_id: "loc-rcv-2" }),
      makeStockRow({ id: "other-item", inventory_item_id: "item-2" }),
    ];

    const result = deductQueued(rows, [queued({ quantity: "5", lot_id: "lot-1" })]);

    expect(Object.fromEntries(result.map((row) => [row.id, row.available_qty]))).toEqual({
      plain: "20",
      lot: "15",
      roll: "20",
      elsewhere: "20",
      "other-item": "20",
    });
  });

  it("ignores an entry the server has refused: it is never sent again, so it holds nothing back", () => {
    const rows = [makeStockRow({ available_qty: "20" })];

    expect(deductQueued(rows, [queued({ quantity: "5" }, "needs_attention")])[0]?.available_qty).toBe("20");
  });

  it("changes nothing when nothing is waiting, and does not mutate what it was given", () => {
    const rows = [makeStockRow()];
    const before = JSON.stringify(rows);

    expect(deductQueued(rows, [])).toEqual(rows);
    deductQueued(rows, [queued({ quantity: "5" })]);
    expect(JSON.stringify(rows)).toBe(before);
  });
});

describe("notices", () => {
  it("has a sentence for every server warning, and none for one it doesn't know", () => {
    expect(warningNotice("geo_dropped:out_of_range")).toMatch(/location/i);
    expect(warningNotice("captured_at_adjusted")).toMatch(/clock/i);
    expect(warningNotice("geo_not_recorded")).toMatch(/location/i);
    expect(warningNotice("something_new")).toBeNull();
  });

  it("says nothing about a suggestion the server ignored: the move stands and the worker can't act on it", () => {
    expect(warningNotice("suggestion_ignored")).toBeNull();
  });

  it("says why a put-away is waiting on the phone", () => {
    expect(queuedNotice("offline")).toMatch(/offline/i);
    expect(queuedNotice("network")).toMatch(/reach the server/i);
    expect(queuedNotice("server")).toMatch(/problem/i);
    expect(queuedNotice("session")).toMatch(/sign back in/i);
  });
});
