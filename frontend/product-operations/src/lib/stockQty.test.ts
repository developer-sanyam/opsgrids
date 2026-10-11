import { describe, expect, it } from "vitest";

import type { StockRow } from "@/api/scan";
import { unprintableReason } from "./labelItems";
import { checkQuantity } from "./stockQty";

const BUCKET = { available_qty: "82.5", uom: "m" } as StockRow;

describe("checkQuantity", () => {
  it("accepts a decimal string the bucket can give", () => {
    expect(checkQuantity("50", BUCKET)).toBe("");
    expect(checkQuantity(" 82.5 ", BUCKET)).toBe("");
    expect(checkQuantity("0.001", BUCKET)).toBe("");
  });

  it("refuses what the server would: floats, exponents, zero, too many decimals", () => {
    for (const bad of ["", "abc", "1e3", "-4", "1.2345", "1,5", "0", "0.000"]) {
      expect(checkQuantity(bad, BUCKET), bad).not.toBe("");
    }
  });

  it("refuses more than is free to move, naming how much is", () => {
    expect(checkQuantity("82.501", BUCKET)).toBe("Only 82.5 m is free to move here");
  });
});

describe("unprintableReason", () => {
  const item = (code: string) => ({ type: "item" as const, id: "1", code, title: "t", subtitle: "" });
  it("lets a printable code through", () => expect(unprintableReason(item("FAB-001"))).toBe(""));
  it("flags a code a bar code cannot carry, or one that is too long", () => {
    expect(unprintableReason(item("café"))).not.toBe("");
    expect(unprintableReason(item("x".repeat(65)))).not.toBe("");
  });
});
