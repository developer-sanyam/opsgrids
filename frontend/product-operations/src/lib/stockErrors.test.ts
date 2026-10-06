import { describe, expect, it } from "vitest";

import { insufficientStock, problem } from "@/test-utils/tracking";
import { describeReservation, operationFieldErrors, stockShortfall } from "./stockErrors";

describe("operationFieldErrors", () => {
  it("puts each server refusal on its field, in words for the pairs the routes raise", () => {
    const error = problem(422, "Validation failed", [
      { field: "lot_id", code: "required", message: "lot required" },
      { field: "roll_id", code: "required", message: "roll required" },
      { field: "to_location_id", code: "different_warehouse", message: "not in the source warehouse" },
      { field: "lot_id", code: "not_tracked", message: "not tracked" },
    ]);
    const mapped = operationFieldErrors(error);
    expect(mapped["lot_id"]).toBe("Choose the lot this stock belongs to.");
    expect(mapped["roll_id"]).toBe("Choose the roll this stock belongs to.");
    expect(mapped["to_location_id"]).toBe(
      "Bin-to-bin transfers stay inside one warehouse. Choose a location in the same warehouse.",
    );
  });

  it("keeps the server's own sentence where it already carries the numbers", () => {
    const split = "children add up to 80 but the roll holds 82.5";
    const merge = "rolls differ in shade";
    expect(
      operationFieldErrors(problem(422, split, [{ field: "children", code: "split_total", message: split }])),
    ).toEqual({ children: split });
    expect(
      operationFieldErrors(problem(422, merge, [{ field: "roll_ids", code: "not_mergeable", message: merge }])),
    ).toEqual({ roll_ids: merge });
  });

  it("maps a duplicate to the field the server named, verbatim", () => {
    const message = "A lot numbered 'LOT-9' already exists";
    expect(
      operationFieldErrors(problem(409, message, [{ field: "lot_no", code: "duplicate", message }], "conflict")),
    ).toEqual({ lot_no: message });
  });

  it("also reports an opening line's field under its bare name, and keeps the first entry", () => {
    const mapped = operationFieldErrors(
      problem(422, "x", [
        { field: "lines.0.quantity", code: "invalid_quantity", message: "must be > 0" },
        { field: "lines.0.quantity", code: "other", message: "second" },
      ]),
    );
    expect(mapped["lines.0.quantity"]).toBe("must be > 0");
    expect(mapped["quantity"]).toBe("must be > 0");
  });

  it("is empty for an error with no field detail, an insufficient-stock 409, or a non-API error", () => {
    expect(operationFieldErrors(new Error("offline"))).toEqual({});
    expect(operationFieldErrors(problem(500, "boom", []))).toEqual({});
    expect(operationFieldErrors(insufficientStock([]))).toEqual({});
  });
});

describe("stockShortfall", () => {
  it("reads the numbers and the reservations standing in the way", () => {
    const shortfall = stockShortfall(
      insufficientStock([
        { reservation_id: "res-1", reference_type: "order", reference_id: "abcdef0123456789", quantity: "60" },
        { reservation_id: "res-2", reference_type: null, reference_id: null, quantity: "5" },
        { nonsense: true },
      ]),
    );
    expect(shortfall).toMatchObject({
      sku: "FAB-100",
      location: "A-01",
      uom: "m",
      requested: "40",
      on_hand: "82.5",
      reserved: "60",
      available: "22.5",
    });
    expect(shortfall?.reservations.map((r) => r.reservation_id)).toEqual(["res-1", "res-2"]);
  });

  it("is null for any other error, including a conflict that is not a shortfall", () => {
    expect(stockShortfall(problem(409, "x", [], "conflict"))).toBeNull();
    expect(stockShortfall(new Error("x"))).toBeNull();
    expect(stockShortfall(null)).toBeNull();
  });

  it("reports a plain shortfall with no reservations as an empty list", () => {
    expect(stockShortfall(insufficientStock([]))?.reservations).toEqual([]);
  });
});

describe("describeReservation", () => {
  it("names the origin by its type and a short reference", () => {
    expect(
      describeReservation({ reservation_id: "r", reference_type: "sales_order", reference_id: "abcdef0123", quantity: "1" }),
    ).toBe("Sales order abcdef01");
    expect(describeReservation({ reservation_id: "r", reference_type: null, reference_id: null, quantity: "1" })).toBe(
      "A reservation",
    );
  });
});
