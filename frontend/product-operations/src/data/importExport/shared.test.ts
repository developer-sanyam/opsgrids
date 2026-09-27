import { describe, expect, it } from "vitest";

import { RowProblem, makeLookup } from "@opsgrids/client";
import type { ImportColumn, ImportContext, LookupSet } from "@opsgrids/client";

import {
  CATEGORY_OPTIONS,
  columnOf,
  optionLabel,
  optionalBoolean,
  optionalNumber,
  optionalOption,
  optionalText,
  refuseAmbiguousChanges,
  requiredOption,
  refuseNearDuplicate,
  requiredText,
  resolveLookupCell,
  resolveReference,
  sameCurrency,
  sameEmail,
  storedReference,
  textOrNull,
} from "./shared";
import type { NameWords } from "./shared";

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
  const words: NameWords = {
    names: "warehouse names",
    existing: (existing, typed) => `existing: ${existing ?? "several"} (typed ${typed})`,
    earlier: (earlier) => `earlier: ${earlier}`,
  };
  /** A context of its own: to the engine that is one file's rows, so it starts with nothing given out. */
  const fileWith = (lookups: LookupSet = { names }): ImportContext => ({
    lookups,
    signal: new AbortController().signal,
  });
  const refuse = (ctx: ImportContext, typed: string, own?: string) =>
    refuseNearDuplicate(ctx, "names", typed, words, own);

  it("lets a name through that nothing existing resembles", () => {
    expect(() => refuse(fileWith(), "East Hub")).not.toThrow();
  });

  it("lets a name spelled exactly like an existing one through, so the server can answer it", () => {
    expect(() => refuse(fileWith(), "Main Warehouse")).not.toThrow();
  });

  it.each(["main warehouse", "MAIN WAREHOUSE", "Main  Warehouse", " main warehouse"])(
    "refuses %j, which differs from an existing name only in capitals or spacing, naming the existing one",
    (typed) => {
      const attempt = () => refuse(fileWith(), typed);
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow(`existing: Main Warehouse (typed ${typed})`);
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
    expect(() => refuse(fileWith({ names: already }), "Pune Plant")).not.toThrow();
    expect(() => refuse(fileWith({ names: already }), "Pune plant")).not.toThrow();
    expect(() => refuse(fileWith({ names: already }), "PUNE PLANT")).toThrow("existing: several (typed PUNE PLANT)");
  });

  it("refuses when the existing names were not loaded, since nothing says the name is free", () => {
    const attempt = () => refuse(fileWith({}), "East Hub");
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("The existing warehouse names could not be loaded, so this row cannot be checked against them.");
    expect(() => refuse(fileWith({}), "main warehouse")).toThrow(RowProblem);
  });

  it("takes a different spelling of the renamed record's own name as a fair change, and only of its own", () => {
    expect(() => refuse(fileWith(), "MAIN WAREHOUSE", "Main Warehouse")).not.toThrow();
    expect(() => refuse(fileWith(), "main  warehouse", "Main Warehouse")).not.toThrow();
    expect(() => refuse(fileWith(), "cold store", "Main Warehouse")).toThrow("existing: Cold Store (typed cold store)");
  });

  describe("across the rows of one file", () => {
    it("refuses a name that differs only in capitals or spacing from one an earlier row gives, naming that one", () => {
      const file = fileWith();
      expect(() => refuse(file, "Delta")).not.toThrow();
      const attempt = () => refuse(file, "delta");
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow("earlier: Delta");
      expect(() => refuse(file, " DELTA ")).toThrow("earlier: Delta");
      expect(() => refuse(file, "De lta")).not.toThrow();
    });

    it("leaves the same spelling twice to the server, which answers the second with its own conflict", () => {
      const file = fileWith();
      expect(() => refuse(file, "Delta")).not.toThrow();
      expect(() => refuse(file, "Delta")).not.toThrow();
    });

    it("starts afresh for the next file, or the next run, which the engine gives a context of its own", () => {
      expect(() => refuse(fileWith(), "Delta")).not.toThrow();
      expect(() => refuse(fileWith(), "delta")).not.toThrow();
    });
  });
});

describe("comparing a cell with a stored record", () => {
  it("takes a currency as the same in other capitals, and never a blank or a non-text cell as the same", () => {
    expect(sameCurrency("inr", "INR")).toBe(true);
    expect(sameCurrency("INR", "INR")).toBe(true);
    expect(sameCurrency("USD", "INR")).toBe(false);
    expect(sameCurrency(null, "INR")).toBe(false);
    expect(sameCurrency("INR", null)).toBe(false);
    expect(sameCurrency(4501, "4501")).toBe(false);
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

  it("finds a column of an entity by key, and fails at load for a key it does not have", () => {
    const columns: ImportColumn[] = [{ key: "a", header: "A", kind: "text", help: "" }];
    expect(columnOf(columns, "a").header).toBe("A");
    expect(() => columnOf(columns, "b")).toThrow('The entity has no column "b"');
  });
});

describe("resolveLookupCell", () => {
  const warehouse: ImportColumn = {
    key: "warehouse",
    header: "Warehouse",
    kind: "text",
    help: "",
    lookup: "warehouses",
    lookupHint: "Add hint.",
    lookupHintUpdate: "Update hint.",
  };
  const ctx: ImportContext = {
    lookups: {
      warehouses: makeLookup([
        { label: "Main Warehouse", value: "Main Warehouse" },
        { label: "Pune Plant", value: "Pune Plant" },
        { label: "Pune plant", value: "Pune plant" },
      ]),
    },
    signal: new AbortController().signal,
  };
  const resolve = resolveLookupCell(warehouse);

  it("resolves a label to the value create would send, whatever the capitals typed", () => {
    expect(resolve("main WAREHOUSE", ctx)).toBe("Main Warehouse");
  });

  it("refuses a label the list does not hold in the engine's words, ending in the column's update hint", () => {
    const attempt = () => resolve("Nowhere", ctx);
    expect(attempt).toThrow(RowProblem);
    expect(attempt).toThrow("'Nowhere' is not one of the accepted values for Warehouse (see the Lists sheet). Update hint.");
  });

  it("does not pick between the entries a label names: it hands the label on as typed, for the row to be judged", () => {
    // Which record the cell means is settled by the stored record's own label (`storedReference`)
    // and, for a change, by `refuseAmbiguousChanges`; neither of them can be done from here.
    expect(resolve("pune plant", ctx)).toBe("pune plant");
    expect(resolve("Pune Plant", ctx)).toBe("Pune Plant");
  });

  it("refuses when the list was never loaded, instead of comparing the label itself", () => {
    expect(() => resolve("Main Warehouse", { ...ctx, lookups: {} })).toThrow(
      "The accepted values for Warehouse could not be loaded, so it cannot be checked",
    );
  });

  it("passes anything but text through", () => {
    expect(resolve(null, ctx)).toBeNull();
  });
});

describe("a lookup label that several entries carry, when updating", () => {
  const categories = makeLookup([
    { label: "METALS", value: "cat-1" },
    { label: "Metals", value: "cat-2" },
    { label: "STEEL", value: "cat-3" },
  ]);
  const column: ImportColumn = {
    key: "category",
    header: "Item category code",
    kind: "text",
    help: "",
    lookup: "categories",
    lookupHintUpdate: "Update hint.",
  };
  const ctx: ImportContext = { lookups: { categories }, signal: new AbortController().signal };

  describe("storedReference", () => {
    it("is the stored value itself when the label it goes by is the only one", () => {
      expect(storedReference(categories, "cat-3")).toBe("cat-3");
    });

    it("is the record's own label when another entry has the same label, since resolve cannot say which is meant", () => {
      expect(storedReference(categories, "cat-1")).toBe("METALS");
      expect(storedReference(categories, "cat-2")).toBe("Metals");
    });

    it("is the stored value when the list does not know it, or was not loaded", () => {
      expect(storedReference(categories, "cat-404")).toBe("cat-404");
      expect(storedReference(undefined, "cat-1")).toBe("cat-1");
    });
  });

  describe("refuseAmbiguousChanges", () => {
    it("refuses a cell that changes the field to a label that several entries carry, in the engine's words", () => {
      const attempt = () => refuseAmbiguousChanges([column], { category: "metals" }, ["Item category code"], ctx);
      expect(attempt).toThrow(RowProblem);
      expect(attempt).toThrow(
        "'metals' matches more than one of the accepted values for Item category code, so it cannot be chosen. Update hint.",
      );
    });

    it("leaves a column the row does not change, a label that resolves, a blank cell and a cleared one alone", () => {
      expect(() => refuseAmbiguousChanges([column], { category: "metals" }, ["Item name"], ctx)).not.toThrow();
      expect(() => refuseAmbiguousChanges([column], { category: "STEEL" }, ["Item category code"], ctx)).not.toThrow();
      expect(() => refuseAmbiguousChanges([column], { category: null }, ["Item category code"], ctx)).not.toThrow();
      expect(() => refuseAmbiguousChanges([column], {}, ["Item category code"], ctx)).not.toThrow();
    });

    it("has nothing to judge a label against when the list was not loaded", () => {
      expect(() => refuseAmbiguousChanges([column], { category: "metals" }, ["Item category code"], { ...ctx, lookups: {} })).not.toThrow();
    });
  });
});
