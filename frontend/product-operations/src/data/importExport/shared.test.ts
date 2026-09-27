import { describe, expect, it } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportContext, LookupSet } from "@opsgrids/client";

import {
  CATEGORY_OPTIONS,
  optionLabel,
  optionalBoolean,
  optionalNumber,
  optionalOption,
  optionalText,
  requiredOption,
  refuseNearDuplicate,
  requiredText,
  resolveLookupCell,
  resolveReference,
  sameCode,
  sameEmail,
  textOrNull,
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

  it("returns the value the API wants, whatever the capitals typed", () => {
    expect(resolveReference(lookups, "warehouses", "MAIN warehouse", "Warehouse")).toBe("Main Warehouse");
  });

  it("fails the row for a name the lookup does not have, naming the column and the name", () => {
    const attempt = () => resolveReference(lookups, "warehouses", "Nowhere", "Warehouse");
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow('Warehouse "Nowhere" does not match exactly one entry on the Lists sheet.');
  });

  it("fails the row for a name more than one record carries rather than picking one", () => {
    expect(() => resolveReference(lookups, "warehouses", "pune plant", "Warehouse")).toThrow(RowProblem);
  });

  it("fails the row when the lookup was never loaded", () => {
    expect(() => resolveReference({}, "warehouses", "Main Warehouse", "Warehouse")).toThrow(RowProblem);
  });
});

describe("refuseNearDuplicate", () => {
  const names = makeLookup([
    { label: "Main Warehouse", value: "Main Warehouse" },
    { label: "Cold Store", value: "Cold Store" },
  ]);
  const sentence = (existing: string | null) => `existing: ${existing ?? "several"}`;

  it("lets a name through that nothing existing resembles", () => {
    expect(() => refuseNearDuplicate(names, "East Hub", sentence)).not.toThrow();
  });

  it("lets a name spelled exactly like an existing one through, so the server can answer it", () => {
    expect(() => refuseNearDuplicate(names, "Main Warehouse", sentence)).not.toThrow();
  });

  it.each(["main warehouse", "MAIN WAREHOUSE", "Main  Warehouse", " main warehouse"])(
    "refuses %j, which differs from an existing name only in capitals or spacing, naming the existing one",
    (typed) => {
      const attempt = () => refuseNearDuplicate(names, typed, sentence);
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow("existing: Main Warehouse");
    },
  );

  it("still lets the exact spelling through when case variants already exist, and refuses a third", () => {
    // Two existing spellings that only differ in case make each other ambiguous, so a lookup no
    // longer lists either; the exact spelling is still recognised.
    const already = makeLookup([
      { label: "Pune Plant", value: "Pune Plant" },
      { label: "Pune plant", value: "Pune plant" },
    ]);
    expect(already.labels).toEqual([]);
    expect(() => refuseNearDuplicate(already, "Pune Plant", sentence)).not.toThrow();
    expect(() => refuseNearDuplicate(already, "Pune plant", sentence)).not.toThrow();
    expect(() => refuseNearDuplicate(already, "PUNE PLANT", sentence)).toThrow("existing: several");
  });

  it("does nothing without a lookup, as when a definition is exercised on its own", () => {
    expect(() => refuseNearDuplicate(undefined, "main warehouse", sentence)).not.toThrow();
  });
});

describe("comparing a cell with a stored record", () => {
  it("takes a code as the same in other capitals or spacing, and never a blank or a non-text cell as the same", () => {
    expect(sameCode("rm-stl-4501", "RM-STL-4501")).toBe(true);
    expect(sameCode(" WH  MAIN ", "wh main")).toBe(true);
    expect(sameCode("RM-STL-4502", "RM-STL-4501")).toBe(false);
    expect(sameCode(null, "RM-STL-4501")).toBe(false);
    expect(sameCode("RM-STL-4501", null)).toBe(false);
    expect(sameCode(4501, "4501")).toBe(false);
  });

  it("takes an email as the same when only its domain differs in capitals, since the cell lower-cases the domain", () => {
    expect(sameEmail("Sales@apex.example", "Sales@Apex.Example")).toBe(true);
    expect(sameEmail("sales@apex.example", "sales@apex.example")).toBe(true);
  });

  it("takes an email as changed when its local part differs in any way, since the server keeps that as typed", () => {
    expect(sameEmail("sales@apex.example", "Sales@apex.example")).toBe(false);
    expect(sameEmail("sales@apex.example", "orders@apex.example")).toBe(false);
    expect(sameEmail("sales@apex.example", "sales@apex.example.in")).toBe(false);
    expect(sameEmail("sales@apex.example", null)).toBe(false);
  });

  it("reads an empty stored text as the null a blank cell parses to", () => {
    expect(textOrNull("")).toBeNull();
    expect(textOrNull("Pune")).toBe("Pune");
  });

  it("resolves a lookup cell to the value create would send, and passes anything but text through", () => {
    const ctx: ImportContext = {
      lookups: { warehouses: makeLookup([{ label: "Main Warehouse", value: "Main Warehouse" }]) },
      signal: new AbortController().signal,
    };
    const resolve = resolveLookupCell("warehouses", "Warehouse");
    expect(resolve("main WAREHOUSE", ctx)).toBe("Main Warehouse");
    expect(() => resolve("Nowhere", ctx)).toThrow('Warehouse "Nowhere" does not match exactly one entry');
    expect(resolve(null, ctx)).toBeNull();
  });
});
