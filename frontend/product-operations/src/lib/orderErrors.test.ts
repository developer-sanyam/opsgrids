import { describe, expect, it } from "vitest";
import { ApiError } from "@opsgrids/client";

import { isStaleOrderConflict, stockShortfalls } from "./orderErrors";

function problemError(status: number, type: string, body: Record<string, unknown> = {}): ApiError {
  return new ApiError(status, "detail", { type: `https://errors.opsgrids.dev/${type}`, status, ...body });
}

describe("stockShortfalls", () => {
  it("reads InsufficientStock's errors[] shape, not the platform's ordinary field-error shape", () => {
    const error = problemError(409, "insufficient-stock", {
      errors: [
        { sku: "RM-STL-4501", uom: "MT", requested: "10", on_hand: "8", reserved: "0", available: "8" },
      ],
    });
    expect(stockShortfalls(error)).toEqual([
      { sku: "RM-STL-4501", uom: "MT", requested: "10", on_hand: "8", reserved: "0", available: "8" },
    ]);
  });

  it("is empty for a plain conflict, even one that carries an errors[] array", () => {
    const error = problemError(409, "conflict", {
      errors: [{ field: "warehouse_id", code: "invalid", message: "nope" }],
    });
    expect(stockShortfalls(error)).toEqual([]);
  });

  it("is empty for a non-ApiError, or one with no errors[] at all", () => {
    expect(stockShortfalls(new Error("network down"))).toEqual([]);
    expect(stockShortfalls(problemError(409, "insufficient-stock"))).toEqual([]);
  });

  it("drops an entry that is missing one of the numbers", () => {
    const error = problemError(409, "insufficient-stock", {
      errors: [{ sku: "RM-STL-4501", requested: "10" }],
    });
    expect(stockShortfalls(error)).toEqual([]);
  });
});

describe("isStaleOrderConflict", () => {
  it("is true for a plain 409 — a stale version, or a status that moved under the caller", () => {
    expect(isStaleOrderConflict(problemError(409, "conflict"))).toBe(true);
  });

  it("is false for an InsufficientStock 409 — that one gets its own shortfall panel, not a reload notice", () => {
    expect(isStaleOrderConflict(problemError(409, "insufficient-stock"))).toBe(false);
  });

  it("is false for anything that is not a 409, or not an ApiError at all", () => {
    expect(isStaleOrderConflict(problemError(422, "validation"))).toBe(false);
    expect(isStaleOrderConflict(new Error("offline"))).toBe(false);
  });
});
