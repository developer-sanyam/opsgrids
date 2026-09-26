import { describe, expect, it } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ExportPage, LookupSet } from "@opsgrids/client";

import {
  CATEGORY_OPTIONS,
  collectAll,
  optionLabel,
  optionalBoolean,
  optionalNumber,
  optionalOption,
  optionalText,
  requiredOption,
  requiredText,
  resolveReference,
} from "./shared";

describe("reading a parsed row", () => {
  it("trims text and treats a blank cell as absent", () => {
    expect(optionalText({ a: "  x  " }, "a")).toBe("x");
    expect(optionalText({ a: "   " }, "a")).toBeNull();
    expect(optionalText({ a: null }, "a")).toBeNull();
    expect(optionalText({}, "a")).toBeNull();
  });

  it("keeps a code the sheet stored as a number as text", () => {
    expect(optionalText({ a: 7209 }, "a")).toBe("7209");
  });

  it("fails a row whose required cell is blank, naming the column", () => {
    expect(() => requiredText({ a: null }, "a", "SKU")).toThrow(RowProblem);
    expect(() => requiredText({ a: null }, "a", "SKU")).toThrow("SKU is required.");
    expect(requiredText({ a: " x " }, "a", "SKU")).toBe("x");
  });

  it("reads numbers and booleans only when the cell holds one", () => {
    expect(optionalNumber({ a: 0 }, "a")).toBe(0);
    expect(optionalNumber({ a: null }, "a")).toBeNull();
    expect(optionalNumber({ a: "3" }, "a")).toBeNull();
    expect(optionalBoolean({ a: false }, "a")).toBe(false);
    expect(optionalBoolean({ a: null }, "a")).toBeNull();
  });

  it("checks an enum cell against the column's options", () => {
    expect(requiredOption({ a: "mro" }, "a", "Category", CATEGORY_OPTIONS)).toBe("mro");
    expect(() => requiredOption({ a: "toys" }, "a", "Category", CATEGORY_OPTIONS)).toThrow(RowProblem);
    expect(() => requiredOption({ a: null }, "a", "Category", CATEGORY_OPTIONS)).toThrow(/Category is required/);
    expect(optionalOption({ a: null }, "a", "Category", CATEGORY_OPTIONS)).toBeNull();
    expect(optionalOption({ a: "mro" }, "a", "Category", CATEGORY_OPTIONS)).toBe("mro");
  });

  it("labels an enum value, and passes through one it does not know", () => {
    expect(optionLabel(CATEGORY_OPTIONS, "raw_materials")).toBe("Raw materials");
    expect(optionLabel(CATEGORY_OPTIONS, "brand_new")).toBe("brand_new");
  });
});

describe("resolveReference", () => {
  const lookups: LookupSet = {
    warehouses: makeLookup([
      { label: "Main Warehouse", value: "Main Warehouse" },
      { label: "Pune Plant", value: "Pune Plant" },
      { label: "Pune plant", value: "Pune plant" },
    ]),
  };
  const problems = {
    unknown: (label: string) => `unknown ${label}`,
    ambiguous: (label: string) => `ambiguous ${label}`,
  };

  it("returns the value the API wants", () => {
    expect(resolveReference(lookups, "warehouses", "MAIN warehouse", problems)).toBe("Main Warehouse");
  });

  it("fails the row for an unknown name", () => {
    expect(() => resolveReference(lookups, "warehouses", "Nowhere", problems)).toThrow("unknown Nowhere");
  });

  it("fails the row for a name more than one record carries", () => {
    expect(() => resolveReference(lookups, "warehouses", "pune plant", problems)).toThrow("ambiguous pune plant");
  });

  it("fails the row when the lookup was never loaded", () => {
    expect(() => resolveReference({}, "warehouses", "Main Warehouse", problems)).toThrow(RowProblem);
  });
});

describe("collectAll", () => {
  function pages(byPage: Record<number, { id: string }[]>, pageCount: number) {
    return (page: number): Promise<ExportPage<{ id: string }>> =>
      Promise.resolve({ items: byPage[page] ?? [], total: 0, pages: pageCount });
  }

  it("walks every page in order", async () => {
    const fetchPage = pages({ 1: [{ id: "a" }, { id: "b" }], 2: [{ id: "c" }] }, 2);
    const rows = await collectAll(fetchPage, new AbortController().signal);
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("keeps a record once when two pages served it", async () => {
    const fetchPage = pages({ 1: [{ id: "a" }, { id: "b" }], 2: [{ id: "b" }, { id: "c" }] }, 2);
    const rows = await collectAll(fetchPage, new AbortController().signal);
    expect(rows.map((r) => r.id)).toEqual(["a", "b", "c"]);
  });

  it("stops when the caller has gone", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(collectAll(pages({ 1: [{ id: "a" }] }, 1), controller.signal)).rejects.toThrow();
  });
});
