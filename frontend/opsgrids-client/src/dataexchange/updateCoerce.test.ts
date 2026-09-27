import { describe, expect, it } from "vitest";

import type { CellInput } from "./cells";
import { CLEAR_REFUSED_MESSAGE, coerceRow } from "./coerce";
import type { RowIntent } from "./coerce";
import { makeProductEntity, makeProductUpdate, productLookups } from "./testkit";
import type { ImportContext } from "./types";

const ctx: ImportContext = { lookups: productLookups, signal: new AbortController().signal };
const entity = makeProductEntity({ update: makeProductUpdate() });

const create: RowIntent = { kind: "create" };
const updateOf = (storedCurrency: string | null = "INR"): RowIntent => ({ kind: "update", storedCurrency });

function coerce(cells: Record<string, CellInput>, intent: RowIntent, target = entity) {
  return coerceRow(target, cells, ctx, intent);
}

describe("[clear] in a cell", () => {
  it("is read before the cell's own type: a whole-number column takes it as 'empty this', not as bad text", () => {
    const { row, errors, cleared } = coerce({ sku: "A-1", quantity: "[clear]", price: "[clear]" }, updateOf());
    expect(errors).toEqual([]);
    expect(row.quantity).toBeNull();
    expect(row.price).toBeNull();
    expect([...cleared].sort()).toEqual(["price", "quantity"]);
  });

  it("is recognised in any case with spaces around it", () => {
    const { cleared, errors } = coerce({ sku: "A-1", email: "  [CLEAR] ", name: "[Clear]" }, updateOf());
    expect(errors).toEqual([]);
    expect([...cleared].sort()).toEqual(["email", "name"]);
  });

  it("is read on a date, an email, a phone, an enum and a lookup column alike", () => {
    const { errors, cleared } = coerce(
      { sku: "A-1", launched: "[clear]", email: "[clear]", phone: "[clear]", status: "[clear]", category: "[clear]" },
      updateOf(),
    );
    expect(errors).toEqual([]);
    expect(cleared.size).toBe(5);
  });

  it("is refused when adding, once, whatever the number of cells that say it", () => {
    const { errors, cleared, row } = coerce({ sku: "A-1", name: "Bracket", email: "[clear]", quantity: "[clear]" }, create);
    expect(errors).toEqual([CLEAR_REFUSED_MESSAGE]);
    expect(CLEAR_REFUSED_MESSAGE).toBe("[clear] only works when updating existing records");
    expect(cleared.size).toBe(0);
    expect(row.email).toBeNull();
  });

  it("is refused when adding even in a required column, without also saying the column is required", () => {
    const { errors } = coerce({ sku: "A-1", name: "[clear]" }, create);
    expect(errors).toEqual([CLEAR_REFUSED_MESSAGE]);
  });

  it("is not text that anything else may hold: only the exact word counts", () => {
    const { errors, cleared } = coerce({ sku: "A-1", name: "clear", email: "[clear] me" }, updateOf());
    expect(cleared.size).toBe(0);
    expect(errors).toEqual(["Supplier email must be a valid email address, e.g. name@example.com"]);
  });
});

describe("a blank cell on a row that updates a record", () => {
  it("leaves the field alone: null, not cleared, and never 'required'", () => {
    const { row, errors, cleared } = coerce({ sku: "", name: null, price: "  " }, updateOf());
    expect(errors).toEqual([]);
    expect(row).toMatchObject({ sku: null, name: null, price: null });
    expect(cleared.size).toBe(0);
  });

  it("is still a required-cell error on a row that creates a record", () => {
    const { errors } = coerce({ sku: "", name: null }, create);
    expect(errors).toEqual(["SKU is required", "Name is required"]);
  });

  it("does not excuse a cell that is filled in wrongly", () => {
    const { errors } = coerce({ sku: "A-1", quantity: "many" }, updateOf());
    expect(errors).toEqual(["Quantity must be a whole number"]);
  });

  it("does not run the entity's cross-column rule, which is written for a complete row", () => {
    const picky = { ...entity, validateRow: () => "Nothing may be blank" };
    expect(coerce({ sku: "A-1", name: null }, updateOf(), picky).errors).toEqual([]);
    expect(coerce({ sku: "A-1", name: "Bracket" }, create, picky).errors).toEqual(["Nothing may be blank"]);
  });
});

describe("the currency of a row that updates a record", () => {
  it("stays blank (null): it is never defaulted to INR, which would re-price the record", () => {
    const { row } = coerce({ sku: "A-1", price: "20" }, updateOf("JPY"));
    expect(row.currency).toBeNull();
  });

  it("converts a money cell in the stored record's currency when the row names none", () => {
    expect(coerce({ price: "20" }, updateOf("JPY")).row.price).toBe(20);
    expect(coerce({ price: "1.234" }, updateOf("KWD")).row.price).toBe(1234);
    expect(coerce({ price: "12.5" }, updateOf("USD")).row.price).toBe(1250);
    expect(coerce({ price: "20" }, updateOf("jpy")).row.price).toBe(20);
  });

  it("defaults to INR only when the record has no currency", () => {
    expect(coerce({ price: "20" }, updateOf(null)).row.price).toBe(2000);
    expect(coerce({ price: "20" }, updateOf("")).row.price).toBe(2000);
  });

  it("uses the currency the row names, and says so in the row", () => {
    const { row } = coerce({ price: "20", currency: "USD" }, updateOf("JPY"));
    expect(row.currency).toBe("USD");
    expect(row.price).toBe(2000);
  });

  it("still refuses a currency that is not a currency", () => {
    expect(coerce({ price: "20", currency: "XX" }, updateOf()).errors).toEqual([
      "'XX' is not one of the allowed values for Currency: INR, USD, JPY, KWD",
    ]);
  });

  it("takes [clear] in the currency cell as no currency named: the money is still the stored currency's", () => {
    const { row, cleared, errors } = coerce({ price: "20", currency: "[clear]" }, updateOf("JPY"));
    expect(errors).toEqual([]);
    expect(row.price).toBe(20);
    expect(cleared.has("currency")).toBe(true);
  });

  it("does not make a required currency column required on an update row", () => {
    const columns = entity.columns.map((column) => (column.key === "currency" ? { ...column, required: true } : column));
    const strict = { ...entity, columns };
    expect(coerce({ price: "20" }, updateOf("JPY"), strict).errors).toEqual([]);
    expect(coerce({ sku: "A", name: "B", price: "20" }, create, strict).errors).toEqual(["Currency is required"]);
  });

  it("still defaults to INR on a row that creates a record", () => {
    const { row } = coerce({ sku: "A-1", name: "Bracket", price: "20" }, create);
    expect(row.currency).toBe("INR");
    expect(row.price).toBe(2000);
  });
});
